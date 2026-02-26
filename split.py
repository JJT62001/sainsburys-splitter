import streamlit as st
from google import genai
from PIL import Image
import json
import requests
import uuid
from datetime import date
from pydantic import BaseModel

# ==============================
# Setup
# ==============================
client = genai.Client(api_key=st.secrets["GEMINI_API_KEY"])
st.set_page_config(page_title="Sainsbury's Splitter", layout="wide")

PEOPLE = ["Joe", "Nic", "Nat"]

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; }

    /* Stepper */
    .stepper {
        display: flex;
        align-items: center;
        gap: 0;
        margin: 0 0 2rem 0;
    }
    .step {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 20px;
        border-radius: 8px;
        font-weight: 600;
        font-size: 0.95rem;
        color: #94a3b8;
        background: transparent;
        white-space: nowrap;
    }
    .step-number {
        width: 28px; height: 28px;
        border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem;
        font-weight: 700;
        background: #e2e8f0;
        color: #94a3b8;
        flex-shrink: 0;
    }
    .step.active {
        color: #0f172a;
        background: #f1f5f9;
        border-radius: 8px;
    }
    .step.active .step-number {
        background: #0f172a;
        color: white;
    }
    .step.done {
        color: #16a34a;
    }
    .step.done .step-number {
        background: #16a34a;
        color: white;
    }
    .step-arrow {
        color: #cbd5e1;
        font-size: 1.1rem;
        padding: 0 4px;
    }

    /* Badges */
    .badge-low { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    .badge-ok  { background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }

    div[data-testid="stMetric"] { background: transparent !important; }
</style>
""", unsafe_allow_html=True)

# ==============================
# Pydantic Schema
# ==============================
class ReceiptItem(BaseModel):
    name: str
    friendly_name: str
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
        "cost": total_amount, "description": description,
        "date": date.today().isoformat(), "group_id": group_id, "split_equally": False,
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

def render_stepper(current_step):
    """Render a visual stepper. current_step: 1=Review, 2=Split, 3=Finalise"""
    steps = ["Review Items", "Split", "Finalise"]
    html  = '<div class="stepper">'
    for i, label in enumerate(steps, 1):
        if i < current_step:
            css = "done"
            num = "✓"
        elif i == current_step:
            css = "active"
            num = str(i)
        else:
            css = ""
            num = str(i)

        html += f'''
        <div class="step {css}">
            <div class="step-number">{num}</div>
            {label}
        </div>'''

        if i < len(steps):
            html += '<span class="step-arrow">›</span>'

    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)

# ==============================
# Sidebar
# ==============================
with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("Discounts")
    apply_15       = st.checkbox("15% Colleague Discount", value=True)
    extra_discount = st.number_input("Extra Discount (%)", min_value=0.0, max_value=100.0, step=0.5, value=0.0)

    if "receipt_items" in st.session_state:
        st.divider()
        # Read live widget values if on review step, otherwise fall back to stored prices
        ai_total = sum(
            float(st.session_state.get(f"price_{i['id']}", i["price"]))
            for i in st.session_state.receipt_items
        )
        st.metric("🧾 AI Total (£)", f"{ai_total:.2f}")

    st.divider()
    if st.button("🔄 New Receipt", use_container_width=True):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ==============================
# Session state init
# ==============================
if "step" not in st.session_state:
    st.session_state.step = 0  # 0=upload, 1=review, 2=split, 3=finalise

# ==============================
# Header
# ==============================
st.title("🛒 Joe, Nic & Nat's Sainsbury's Splitter")

# ==============================
# STEP 0 — Upload & Scan
# ==============================
if st.session_state.step == 0:
    uploaded_file = st.file_uploader("Upload Receipt Photo", type=["jpg", "jpeg", "png"])

    if uploaded_file:
        col1, col2 = st.columns([1, 2])
        img = Image.open(uploaded_file)
        col1.image(img, caption="Receipt", use_container_width=True)

        with col2:
            st.markdown("#### Receipt uploaded ✓")
            st.caption("Click below to let Gemini read the items.")
            if st.button("🔍 Analyse Receipt", type="primary", use_container_width=True):
                with st.spinner("Gemini is reading the receipt..."):
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

                    Also add a friendly_name field: a short, human-readable version of the receipt name.
                    Receipt names are often truncated codes — decode them into plain English.
                    Examples:
                      "chicken s cub x10"  → "Chicken Stock Cubes x10"
                      "TTD SHNK BEEF"      → "Taste the Difference Beef Shank"
                      "SO org chdr mtr"    → "Sainsbury's Organic Cheddar Mature"
                      "WHLML Med LOAF"     → "Wholemeal Medium Loaf"
                    If the name is already clear, just return it tidied up with correct capitalisation.
                    """
                    try:
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
                        st.session_state.step = 1
                        if low_conf_count:
                            st.session_state.low_conf_count = low_conf_count
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

# ==============================
# STEPS 1-3 — Main flow
# ==============================
else:
    render_stepper(st.session_state.step)

    # ---- STEP 1: Review ----
    if st.session_state.step == 1:
        n_items = len(st.session_state.receipt_items)
        st.caption(f"{n_items} items found — edit names/prices or delete anything wrong.")

        if st.session_state.get("low_conf_count"):
            st.warning(f"⚠️ {st.session_state.low_conf_count} item(s) flagged as uncertain — check the badges below.")

        updated_items = []
        for item in st.session_state.receipt_items:
            item_id = item["id"]
            conf    = float(item.get("confidence", 1.0))
            badge   = (
                '<span class="badge-low">⚠ Check me</span>'
                if conf < 0.75 else
                '<span class="badge-ok">✓ Clear</span>'
            )
            cols   = st.columns([3, 2, 1, 1])
            name   = cols[0].text_input("Name", value=item.get("friendly_name", item["name"]), key=f"name_{item_id}", label_visibility="collapsed")
            cols[0].markdown(badge + f' <span style="color:#94a3b8;font-size:0.72rem">{item["name"]}</span>', unsafe_allow_html=True)
            price  = cols[1].number_input("Price", value=float(item["price"]), step=0.01,
                                          key=f"price_{item_id}", label_visibility="collapsed")
            delete = cols[2].button("❌", key=f"delete_{item_id}")
            if not delete:
                updated_items.append({"id": item_id, "name": name, "price": price, "confidence": conf})
            else:
                st.session_state.assignments.pop(item_id, None)

        st.session_state.receipt_items = updated_items

        st.divider()
        st.markdown("**➕ Add missing item**")
        c1, c2, c3 = st.columns([3, 2, 1])
        new_name  = c1.text_input("Item name", label_visibility="collapsed", placeholder="Item name")
        new_price = c2.number_input("Price", min_value=0.0, step=0.01, label_visibility="collapsed")
        if c3.button("Add"):
            if new_name:
                new_id = str(uuid.uuid4())
                st.session_state.receipt_items.append({"id": new_id, "name": new_name, "price": new_price, "confidence": 1.0})
                st.session_state.assignments[new_id] = PEOPLE[:]
                st.rerun()
            else:
                st.warning("Enter an item name.")

        st.divider()
        if st.button("Next → Split Items", type="primary", use_container_width=True):
            st.session_state.step = 2
            st.rerun()

    # ---- STEP 2: Split ----
    elif st.session_state.step == 2:
        st.caption("Everyone is in by default — remove people from items they didn't share.")

        for item in st.session_state.receipt_items:
            item_id    = item["id"]
            conf       = float(item.get("confidence", 1.0))
            conf_badge = " ⚠️" if conf < 0.75 else ""

            cols = st.columns([3, 3, 1])
            display_name = item.get("friendly_name", item["name"])
            cols[0].markdown(
                f"**{display_name}{conf_badge}** &nbsp; £{float(item['price']):.2f}"
                f'<br><span style="color:#94a3b8;font-size:0.72rem">{item["name"]}</span>',
                unsafe_allow_html=True
            )

            if cols[2].button("All", key=f"all_{item_id}"):
                st.session_state[f"split_{item_id}"] = PEOPLE[:]

            if f"split_{item_id}" not in st.session_state:
                st.session_state[f"split_{item_id}"] = st.session_state.assignments.get(item_id, PEOPLE[:])

            selected = cols[1].multiselect(
                "Who's in?", PEOPLE,
                key=f"split_{item_id}",
                label_visibility="collapsed"
            )
            st.session_state.assignments[item_id] = selected

        st.divider()
        nav = st.columns(2)
        if nav[0].button("← Back to Review", use_container_width=True):
            st.session_state.step = 1
            st.rerun()
        if nav[1].button("Next → Finalise", type="primary", use_container_width=True):
            st.session_state.step = 3
            st.rerun()

    # ---- STEP 3: Finalise ----
    elif st.session_state.step == 3:
        st.subheader("Who paid today?")
        payer = st.radio("Payer", PEOPLE, horizontal=True, label_visibility="collapsed")

        if st.button("✅ Finalise Split", type="primary", use_container_width=True):
            exact_totals = {p: 0.0 for p in PEOPLE}
            for item in st.session_state.receipt_items:
                item_id = item["id"]
                split   = st.session_state.assignments.get(item_id, [])
                if not split:
                    continue
                price = discounted_price(float(item["price"]), apply_15, extra_discount)
                share = price / len(split)
                for person in split:
                    exact_totals[person] += share
            final_totals = {p: round(v * 100) for p, v in exact_totals.items()}
            st.session_state.final_totals = final_totals
            st.session_state.payer        = payer

        if "final_totals" in st.session_state:
            final_totals = st.session_state.final_totals
            payer        = st.session_state.payer
            grand        = sum(final_totals.values())

            st.divider()
            cols = st.columns(len(PEOPLE))
            for col, person in zip(cols, PEOPLE):
                col.metric(label=person, value=f"£{final_totals[person] / 100:.2f}")
            st.metric(label="🧾 Grand Total", value=f"£{grand / 100:.2f}")

            st.divider()
            st.subheader("📲 Send to Splitwise")
            expense_name = st.text_input("Expense name", value="Sainsbury's")

            if st.button("➕ Create Splitwise Expense", type="primary", use_container_width=True):
                with st.spinner("Sending to Splitwise..."):
                    try:
                        result   = create_splitwise_expense(expense_name, grand, payer, final_totals)
                        expenses = result.get("expenses", [])
                        errors   = result.get("errors", {})
                        if expenses and not errors:
                            exp = expenses[0]
                            st.success(f"✅ '{exp['description']}' added! {payer} paid £{grand/100:.2f}.")
                            st.balloons()
                        else:
                            st.error(f"Splitwise error: {result}")
                    except Exception as e:
                        st.error(f"Failed: {e}")

        st.divider()
        if st.button("← Back to Split", use_container_width=True):
            st.session_state.step = 2
            st.rerun()
