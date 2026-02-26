import streamlit as st
from google import genai
from PIL import Image
import json
import random
import requests
import uuid
from datetime import date
from pydantic import BaseModel

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
    .badge-low  { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    .badge-ok   { background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    div[data-testid="stMetric"] { background: transparent !important; }
</style>
""", unsafe_allow_html=True)

st.title("🛒 Joe, Nic & Nat's Sainsbury's Splitter")

PEOPLE = ["Joe", "Nic", "Nat"]

# ==============================
# Pydantic schema — forces Gemini to return valid structured data
# ==============================
class ReceiptItem(BaseModel):
    name: str
    price: float
    confidence: float

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

            # Resize to cap bandwidth without losing legibility
            img.thumbnail((1500, 1500))

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

            CRITICAL - Cancelled items:
            Some items are cancelled and appear as three lines: the item with a positive price, an "ITEM CANCELLED" line, then the same item with a negative price.
            You MUST ignore all three lines entirely — do not include the item in the output at all.

            Example on receipt:
              Bread        2.00
              ITEM CANCELLED
              Bread       -2.00
            Correct output: do not include Bread at all
            WRONG output: {"name": "Bread", "price": 2.00} or {"name": "Bread", "price": 0.00}

            Rules:
            - Never include the Nectar saving line as its own item.
            - Always return the post-saving (cheaper) price, not the shelf price.
            - Never include cancelled items or their reversal lines.
            - Ignore: Total, Subtotal, Bag charge, card payment, and change lines.

            For each item add a confidence field:
              - 1.0 = name and price are clearly legible
              - 0.5 = name or price had to be guessed (blurry, cut off, ambiguous)
              - 0.0 = very uncertain
            """

            try:
                # Structured output — Gemini forced to return valid typed JSON, no regex scraping
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=[prompt, img],
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": list[ReceiptItem],
                        "temperature": 0.1,
                    }
                )

                items = json.loads(response.text)

                # UUID-based tracking — prevents index shift bugs when items are deleted
                st.session_state.receipt_items = []
                st.session_state.assignments   = {}

                low_conf_count = 0
                for item in items:
                    item_id    = str(uuid.uuid4())
                    item["id"] = item_id
                    st.session_state.receipt_items.append(item)
                    st.session_state.assignments[item_id] = PEOPLE[:]
                    if item.get("confidence", 1.0) < 0.75:
                        low_conf_count += 1

                st.success(f"Receipt analysed - {len(items)} items found!")
                if low_conf_count:
                    st.warning(f"⚠️ {low_conf_count} item(s) flagged as uncertain - please double-check them below.")

            except Exception as e:
                st.error(f"Error analysing receipt: {e}")

# ==============================
# Main App Section
# ==============================
if "receipt_items" in st.session_state:

    st.divider()
    st.subheader("Review & Edit Items")

    updated_items = []
    for item in st.session_state.receipt_items:
        item_id = item["id"]
        conf    = float(item.get("confidence", 1.0))
        badge   = (
            '<span class="badge-low">⚠ Check me</span>'
            if conf < 0.75 else
            '<span class="badge-ok">✓ Clear</span>'
        )

        cols = st.columns([3, 2, 1, 1])
        name  = cols[0].text_input("Item Name", value=item["name"], key=f"name_{item_id}", label_visibility="collapsed")
        cols[0].markdown(badge, unsafe_allow_html=True)
        price = cols[1].number_input("Price", value=float(item["price"]), step=0.01,
                                     key=f"price_{item_id}", label_visibility="collapsed")
        delete = cols[2].button("❌", key=f"delete_{item_id}")

        if not delete:
            updated_items.append({"id": item_id, "name": name, "price": price, "confidence": conf})
        else:
            st.session_state.assignments.pop(item_id, None)

    st.session_state.receipt_items = updated_items

    # ==============================
    # Add Missing Item
    # ==============================
    st.markdown("### ➕ Add Missing Item")
    c1, c2, c3 = st.columns([3, 2, 1])
    new_name  = c1.text_input("New Item Name", label_visibility="collapsed", placeholder="Item name")
    new_price = c2.number_input("New Item Price", min_value=0.0, step=0.01, label_visibility="collapsed")
    if c3.button("Add Item"):
        if new_name:
            new_id = str(uuid.uuid4())
            st.session_state.receipt_items.append({"id": new_id, "name": new_name, "price": new_price, "confidence": 1.0})
            st.session_state.assignments[new_id] = PEOPLE[:]
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
    apply_15       = col1.checkbox("Apply 15% Colleague Discount", value=True)
    extra_discount = col2.number_input("Additional Discount (%)", min_value=0.0, max_value=100.0,
                                       step=0.5, value=0.0)
    st.info(f"Active Discounts:  •  15% Colleague: {'ON' if apply_15 else 'OFF'}  •  Extra: {extra_discount}%")

    # ==============================
    # Splitting Section
    # ==============================
    st.divider()
    st.subheader("Split the Items")

    for item in st.session_state.receipt_items:
        item_id    = item["id"]
        conf       = float(item.get("confidence", 1.0))
        conf_badge = " ⚠️" if conf < 0.75 else ""

        cols = st.columns([3, 3, 1])
        cols[0].markdown(f"**{item['name']}{conf_badge}** &nbsp; £{float(item['price']):.2f}",
                         unsafe_allow_html=True)

        # All button writes to widget key BEFORE multiselect renders — avoids default= conflict
        if cols[2].button("All", key=f"all_{item_id}"):
            st.session_state[f"split_{item_id}"] = PEOPLE[:]

        # Seed widget key from assignments on first render only
        if f"split_{item_id}" not in st.session_state:
            st.session_state[f"split_{item_id}"] = st.session_state.assignments.get(item_id, PEOPLE[:])

        selected = cols[1].multiselect(
            "Who's in?",
            PEOPLE,
            key=f"split_{item_id}",
            label_visibility="collapsed"
        )

        st.session_state.assignments[item_id] = selected

    # ==============================
    # Who Paid + Finalise
    # ==============================
    st.divider()
    st.subheader("Who paid today?")
    payer = st.radio("Select payer", PEOPLE, horizontal=True, label_visibility="collapsed")

    if st.button("✅ Finalise Split", type="primary"):
        final_totals = {p: 0 for p in PEOPLE}

        for item in st.session_state.receipt_items:
            item_id   = item["id"]
            split     = st.session_state.assignments.get(item_id, [])
            if not split:
                continue
            price     = discounted_price(float(item["price"]), apply_15, extra_discount)
            pennies   = round(price * 100)
            share     = pennies // len(split)
            remainder = pennies % len(split)
            for person in split:
                final_totals[person] += share
            if remainder > 0:
                for winner in random.sample(split, k=remainder):
                    final_totals[winner] += 1

        st.session_state.final_totals = final_totals
        st.session_state.payer        = payer

    if "final_totals" in st.session_state:
        final_totals = st.session_state.final_totals
        payer        = st.session_state.payer

        st.balloons()
        st.success("### 🎉 Final Totals")

        cols = st.columns(len(PEOPLE))
        for col, person in zip(cols, PEOPLE):
            col.metric(label=person, value=f"£{final_totals[person] / 100:.2f}")

        grand = sum(final_totals.values())
        st.metric(label="🧾 Grand Total (all splits)", value=f"£{grand / 100:.2f}")

        st.divider()
        st.subheader("📲 Send to Splitwise")
        expense_name = st.text_input("Expense name", value="Sainsbury's")

        if st.button("➕ Create Splitwise Expense", type="primary"):
            with st.spinner("Creating expense in Splitwise..."):
                try:
                    result   = create_splitwise_expense(expense_name, grand, payer, final_totals)
                    expenses = result.get("expenses", [])
                    errors   = result.get("errors", {})
                    if expenses and not errors:
                        exp = expenses[0]
                        st.success(f"✅ '{exp['description']}' added to Splitwise! {payer} paid £{grand/100:.2f}.")
                    else:
                        st.error(f"Splitwise returned an error: {result}")
                except Exception as e:
                    st.error(f"Failed to create expense: {e}")
