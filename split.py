import streamlit as st
from google import genai
from PIL import Image
import json
import random
import requests
from datetime import date

# ==============================
# Setup Gemini Client (Secure)
# ==============================
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])

st.set_page_config(page_title="Sainsbury's Splitter", layout="wide")

# ==============================
# Styling
# ==============================
st.markdown("""
<style>
    /* Confidence warning badge */
    .badge-low  { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    .badge-ok   { background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }

    div[data-testid="stMetric"] { background: transparent !important; }
</style>
""", unsafe_allow_html=True)

st.title("🛒 Joe, Nic & Nat's Sainsbury's Splitter")

PEOPLE = ["Joe", "Nic", "Nat"]
# ==============================
# Helpers
# ==============================
def discounted_price(price: float, apply_15: bool, extra_discount: float) -> float:
    p = price
    if apply_15:
        p *= 0.85
    if extra_discount > 0:
        p *= (1 - extra_discount / 100)
    return p



# ==============================
# Splitwise Helper
# ==============================
def create_splitwise_expense(description, total_pennies, payer, final_totals):
    api_key  = st.secrets["SPLITWISE_API_KEY"]
    group_id = st.secrets["SPLITWISE_GROUP_ID"]
    user_ids = {
        "Joe": str(st.secrets["SPLITWISE_USER_JOE"]),
        "Nic": str(st.secrets["SPLITWISE_USER_NIC"]),
        "Nat": str(st.secrets["SPLITWISE_USER_NAT"]),
    }

    total_amount = f"{total_pennies / 100:.2f}"

    payload = {
        "cost":          total_amount,
        "description":   description,
        "date":          date.today().isoformat(),
        "group_id":      group_id,
        "split_equally": False,
    }

    # Payer paid the full amount
    payer_id = user_ids[payer]
    payload["users__0__user_id"]    = payer_id
    payload["users__0__paid_share"] = total_amount
    payload["users__0__owed_share"] = f"{final_totals[payer] / 100:.2f}"

    idx = 1
    for person, uid in user_ids.items():
        if person == payer:
            continue
        payload[f"users__{idx}__user_id"]    = uid
        payload[f"users__{idx}__paid_share"] = "0.00"
        payload[f"users__{idx}__owed_share"] = f"{final_totals[person] / 100:.2f}"
        idx += 1

    response = requests.post(
        "https://secure.splitwise.com/api/v3.0/create_expense",
        headers={"Authorization": f"Bearer {api_key}"},
        data=payload
    )
    return response.json()

# ==============================
# Reset / New Receipt
# ==============================
if st.button("🔄 Start New Receipt"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

uploaded_file = st.file_uploader("Upload Receipt Photo", type=["jpg", "jpeg", "png"])

# ==============================
# Receipt Analysis
# ==============================
if uploaded_file:
    img = Image.open(uploaded_file)
    st.image(img, caption="Receipt Scan", width=400)

    if st.button("Analyze Receipt"):
        with st.spinner("Gemini is reading the receipt..."):

            prompt = """
            Extract items from this Sainsbury's receipt and return the FINAL price the customer actually paid for each item.

            CRITICAL - Nectar / loyalty savings:
            Some items are followed by a line saying "Nectar Price Saving", "Nectar Saver", or similar, with a NEGATIVE amount (e.g. -1.00).
            You MUST subtract that saving from the item directly above it to get the real price paid.

            Example on receipt:
              Yorkshire Tea Bags      3.00
              Nectar Price Saving    -1.00
            Correct output: {"name": "Yorkshire Tea Bags", "price": 2.00}
            WRONG output:   {"name": "Yorkshire Tea Bags", "price": 3.00}

            Rules:
            - Never include the Nectar saving line as its own item.
            - Always return the post-saving (cheaper) price, not the shelf price.
            - Ignore: Total, Subtotal, Bag charge, card payment, and change lines.

            For each item add a "confidence" field:
              - 1.0 = name and price are clearly legible
              - 0.5 = name or price had to be guessed (blurry, cut off, ambiguous)
              - 0.0 = very uncertain

            Return ONLY a valid JSON list, no markdown, no extra text:
            [
              {"name": "Item Name", "price": 1.50, "confidence": 1.0}
            ]
            """

            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[
                        {
                            "role": "user",
                            "parts": [
                                {"text": prompt},
                                {
                                    "inline_data": {
                                        "mime_type": "image/png",
                                        "data": uploaded_file.getvalue()
                                    }
                                }
                            ]
                        }
                    ]
                )

                raw_text = response.text or ""
                raw_text = raw_text.replace("```json", "").replace("```", "").strip()
                start_index = raw_text.find("[")
                end_index = raw_text.rfind("]") + 1

                if start_index == -1 or end_index == 0:
                    st.error("Couldn't find valid JSON list in AI response.")
                    st.write(raw_text)
                    st.stop()

                items = json.loads(raw_text[start_index:end_index])

                # Back-fill confidence if model forgot
                for item in items:
                    item.setdefault("confidence", 1.0)

                st.session_state.receipt_items = items
                low_conf = [i for i in items if i["confidence"] < 0.75]
                st.success(f"Receipt analysed — {len(items)} items found!")
                if low_conf:
                    st.warning(f"⚠️ {len(low_conf)} item(s) flagged as uncertain — please double-check them below.")

            except Exception as e:
                st.error(f"Error analysing receipt: {e}")

# ==============================
# Main App Section
# ==============================
if "receipt_items" in st.session_state:

    st.divider()
    st.subheader("Review & Edit Items")

    updated_items = []
    for i, item in enumerate(st.session_state.receipt_items):
        conf = float(item.get("confidence", 1.0))
        badge = (
            '<span class="badge-low">⚠ Check me</span>'
            if conf < 0.75 else
            '<span class="badge-ok">✓ Clear</span>'
        )

        cols = st.columns([3, 2, 1, 1])

        name = cols[0].text_input("Item Name", value=item["name"], key=f"name_{i}", label_visibility="collapsed")
        cols[0].markdown(badge, unsafe_allow_html=True)

        price = cols[1].number_input("Price (£)", value=float(item["price"]), step=0.01,
                                     key=f"price_{i}", label_visibility="collapsed")

        delete = cols[2].button("❌", key=f"delete_{i}")

        if not delete:
            updated_items.append({"name": name, "price": price, "confidence": conf})

    st.session_state.receipt_items = updated_items

    # ==============================
    # Add Missing Item
    # ==============================
    st.markdown("### ➕ Add Missing Item")
    c1, c2, c3 = st.columns([3, 2, 1])
    new_name  = c1.text_input("New Item Name",      label_visibility="collapsed", placeholder="Item name")
    new_price = c2.number_input("New Item Price (£)", min_value=0.0, step=0.01, label_visibility="collapsed")
    if c3.button("Add Item"):
        if new_name:
            st.session_state.receipt_items.append({"name": new_name, "price": new_price, "confidence": 1.0})
            st.rerun()
        else:
            st.warning("Please enter an item name.")

    # ==============================
    # Receipt Total Checker
    # ==============================
    st.divider()
    st.subheader("Receipt Total Check")
    ai_total = sum(float(item["price"]) for item in st.session_state.receipt_items)
    col1, col2 = st.columns(2)
    actual_total = col1.number_input("Enter Actual Receipt Total (£)", min_value=0.0, step=0.01)
    col2.metric("AI Parsed Total (£)", f"{ai_total:.2f}")
    if actual_total > 0:
        difference = round(actual_total - ai_total, 2)
        if abs(difference) < 0.01:
            st.success("✅ Totals match perfectly!")
        elif difference > 0:
            st.error(f"⚠ AI total is £{difference:.2f} LOWER than receipt.")
        else:
            st.error(f"⚠ AI total is £{abs(difference):.2f} HIGHER than receipt.")

    # ==============================
    # Discount Options
    # ==============================
    st.divider()
    st.subheader("Discount Settings")
    col1, col2 = st.columns(2)
    apply_15      = col1.checkbox("Apply 15% Colleague Discount", value=True)
    extra_discount = col2.number_input("Additional Discount (%)", min_value=0.0, max_value=100.0,
                                       step=0.5, value=0.0)
    st.info(f"Active Discounts:  •  15% Colleague: {'ON' if apply_15 else 'OFF'}  •  Extra: {extra_discount}%")

    # ==============================
    # Splitting Section
    # ==============================
    st.divider()
    st.subheader("Split the Items")

    # Initialise persistent assignment state
    n = len(st.session_state.receipt_items)
    if "assignments" not in st.session_state or len(st.session_state.assignments) != n:
        st.session_state.assignments = [["Joe", "Nic", "Nat"] for _ in range(n)]

    assignments = []

    for i, item in enumerate(st.session_state.receipt_items):
        cols = st.columns([3, 3, 1])

        conf = float(item.get("confidence", 1.0))
        conf_badge = " ⚠️" if conf < 0.75 else ""
        cols[0].markdown(f"**{item['name']}{conf_badge}** &nbsp; £{float(item['price']):.2f}",
                         unsafe_allow_html=True)

        # "All" quick-assign button — must set widget key BEFORE the widget renders
        if cols[2].button("All", key=f"all_{i}"):
            st.session_state[f"split_{i}"] = PEOPLE[:]

        # Seed the widget key from assignments if not yet in session state
        if f"split_{i}" not in st.session_state:
            st.session_state[f"split_{i}"] = st.session_state.assignments[i]

        selected = cols[1].multiselect(
            "Who's in?",
            PEOPLE,
            key=f"split_{i}",
            label_visibility="collapsed"
        )

        st.session_state.assignments[i] = selected
        assignments.append({"price": float(item["price"]), "split": selected})

    # ==============================
    # Who Paid + Finalise
    # ==============================
    st.divider()
    st.subheader("Who paid today?")
    payer = st.radio("Select payer", PEOPLE, horizontal=True, label_visibility="collapsed")

    if st.button("✅ Finalise Split", type="primary"):
        final_totals = {p: 0 for p in PEOPLE}

        for item in assignments:
            if not item["split"]:
                continue
            price = discounted_price(item["price"], apply_15, extra_discount)
            pennies = round(price * 100)
            share = pennies // len(item["split"])
            remainder = pennies % len(item["split"])
            for person in item["split"]:
                final_totals[person] += share
            if remainder > 0:
                for winner in random.sample(item["split"], k=remainder):
                    final_totals[winner] += 1

        st.session_state.final_totals = final_totals
        st.session_state.payer = payer

    if "final_totals" in st.session_state:
        final_totals = st.session_state.final_totals
        payer = st.session_state.payer

        st.balloons()
        st.success("### 🎉 Final Totals")

        cols = st.columns(len(PEOPLE))
        for col, person in zip(cols, PEOPLE):
            col.metric(label=person, value=f"£{final_totals[person] / 100:.2f}")

        grand = sum(final_totals.values())
        st.metric(label="🧾 Grand Total (all splits)", value=f"£{grand / 100:.2f}")

        st.divider()
        st.subheader("📲 Send to Splitwise")
        expense_name = st.text_input(
            "Expense name",
            value="Sainsbury's"
        )

        if st.button("➕ Create Splitwise Expense", type="primary"):
            with st.spinner("Creating expense in Splitwise..."):
                try:
                    result = create_splitwise_expense(expense_name, grand, payer, final_totals)
                    expenses = result.get("expenses", [])
                    errors = result.get("errors", {})
                    if expenses and not errors:
                        exp = expenses[0]
                        st.success(f"✅ '{exp['description']}' added to Splitwise! {payer} paid £{grand/100:.2f}.")
                    else:
                        st.error(f"Splitwise returned an error: {result}")
                except Exception as e:
                    st.error(f"Failed to create expense: {e}")
