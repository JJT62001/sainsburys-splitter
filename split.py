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
    .stepper { display: flex; align-items: center; gap: 0; margin: 0 0 2rem 0; }
    .step {
        display: flex; align-items: center; gap: 10px;
        padding: 10px 20px; border-radius: 8px;
        font-weight: 600; font-size: 0.95rem;
        color: #94a3b8; background: transparent; white-space: nowrap;
    }
    .step-number {
        width: 28px; height: 28px; border-radius: 50%;
        display: flex; align-items: center; justify-content: center;
        font-size: 0.8rem; font-weight: 700;
        background: #e2e8f0; color: #94a3b8; flex-shrink: 0;
    }
    .step.active { color: #0f172a; background: #f1f5f9; }
    .step.active .step-number { background: #0f172a; color: white; }
    .step.done { color: #16a34a; }
    .step.done .step-number { background: #16a34a; color: white; }
    .step-arrow { color: #cbd5e1; font-size: 1.1rem; padding: 0 4px; }

    /* Badges */
    .badge-low { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    .badge-ok  { background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }

    /* Splitwise summary box */
    .sw-summary {
        background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px;
        padding: 16px 20px; margin-bottom: 16px;
    }
    .sw-summary-row { display: flex; justify-content: space-between; padding: 4px 0; font-size: 0.95rem; }
    .sw-summary-row.payer { font-weight: 700; color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 8px; margin-bottom: 4px; }

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
def discounted_price(price: float, colleague_discount: float, extra_discount: float) -> float:
    p = price
    if colleague_discount > 0:
        p *= (1 - colleague_discount / 100)
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
    steps = ["Review Items", "Split", "Finalise"]
    html  = '<div class="stepper">'
    for i, label in enumerate(steps, 1):
        if i < current_step:
            css, num = "done", "✓"
        elif i == current_step:
            css, num = "active", str(i)
        else:
            css, num = "", str(i)
        html += f'<div class="step {css}"><div class="step-number">{num}</div>{label}</div>'
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
    colleague_option = st.selectbox(
        "Colleague Discount",
        options=["10% — Mon to Thu", "15% — Fri & Sat", "20% — Special offer", "None"],
        index=1,
    )
    colleague_discount_map = {
        "10% — Mon to Thu": 10.0,
        "15% — Fri & Sat": 15.0,
        "20% — Special offer": 20.0,
        "None": 0.0,
    }
    colleague_discount = colleague_discount_map[colleague_option]
    extra_discount = st.number_input("Extra Discount (%)", min_value=0.0, max_value=100.0, step=0.5, value=0.0)

    parts = []
    if colleague_discount > 0:
        parts.append(f"{colleague_discount:.0f}% colleague")
    if extra_discount > 0:
        parts.append(f"{extra_discount:.0f}% extra")
    if parts:
        st.caption(f"🏷️ Active: {' + '.join(parts)}")

    if "receipt_items" in st.session_state:
        st.divider()
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
    st.session_state.step = 0

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
                            model="gemini-2.5-flash-lite",
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
                        st.session_state.cleared_items = set()
                        low_conf_count = 0
                        for item in items:
                            item_id    = str(uuid.uuid4())
                            item["id"] = item_id
                            st.session_state.receipt_items.append(item)
                            st.session_state.assignments[item_id] = PEOPLE[:]
                            if item.get("confidence", 1.0) < 0.75:
                                low_conf_count += 1
                        st.session_state.step           = 1
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
        st.caption(f"{n_items} items found — check the names and prices, fix anything that looks off, then move on.")

        if "cleared_items" not in st.session_state:
            st.session_state.cleared_items = set()

        cleared   = st.session_state.cleared_items
        remaining = sum(
            1 for i in st.session_state.receipt_items
            if float(i.get("confidence", 1.0)) < 0.75 and i["id"] not in cleared
        )
        if remaining > 0:
            st.warning(f"⚠️ {remaining} item(s) look uncertain — check the amber badges before moving on.")
        elif st.session_state.get("low_conf_count", 0) > 0:
            st.success("✅ All flagged items checked — looking good!")

        updated_items = []
        for item in st.session_state.receipt_items:
            item_id = item["id"]
            conf    = float(item.get("confidence", 1.0))
            is_low  = conf < 0.75 and item_id not in cleared

            badge = (
                '<span class="badge-low">⚠ Check me</span>'
                if is_low else
                '<span class="badge-ok">✓ Clear</span>'
            )

            # Only show receipt code in grey on flagged items — it's noise once verified
            sub = f' <span style="color:#94a3b8;font-size:0.72rem">{item["name"]}</span>' if is_low else ""

            cols  = st.columns([3, 2, 1, 1])
            name  = cols[0].text_input("Name", value=item.get("friendly_name", item["name"]),
                                       key=f"name_{item_id}", label_visibility="collapsed")
            cols[0].markdown(badge + sub, unsafe_allow_html=True)
            price = cols[1].number_input("Price", value=float(item["price"]), step=0.01,
                                         key=f"price_{item_id}", label_visibility="collapsed")

            if is_low:
                if cols[2].button("✓", key=f"clear_{item_id}", help="Mark as checked"):
                    st.session_state.cleared_items.add(item_id)
                    st.rerun()
                delete = cols[3].button("❌", key=f"delete_{item_id}")
            else:
                delete = cols[2].button("❌", key=f"delete_{item_id}")

            if not delete:
                updated_items.append({"id": item_id, "name": item["name"],
                                      "friendly_name": name, "price": price, "confidence": conf})
            else:
                st.session_state.assignments.pop(item_id, None)
                st.session_state.cleared_items.discard(item_id)

        st.session_state.receipt_items = updated_items

        st.divider()
        st.markdown("**➕ Add missing item**")
        c1, c2, c3 = st.columns([3, 2, 1])
        new_name  = c1.text_input("Item name", label_visibility="collapsed", placeholder="Item name")
        new_price = c2.number_input("Price", min_value=0.0, step=0.01, label_visibility="collapsed")
        if c3.button("Add"):
            if new_name:
                new_id = str(uuid.uuid4())
                st.session_state.receipt_items.append({
                    "id": new_id, "name": new_name, "friendly_name": new_name,
                    "price": new_price, "confidence": 1.0
                })
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

        unassigned = sum(
            1 for item in st.session_state.receipt_items
            if not st.session_state.get(f"split_{item['id']}", st.session_state.assignments.get(item["id"], PEOPLE[:]))
        )
        if unassigned > 0:
            st.warning(f"⚠️ {unassigned} item(s) have nobody assigned — they won't be included in the split.")

        for item in st.session_state.receipt_items:
            item_id      = item["id"]
            conf         = float(item.get("confidence", 1.0))
            cleared_items = st.session_state.get("cleared_items", set())
            conf_badge   = " ⚠️" if conf < 0.75 and item_id not in cleared_items else ""
            display_name = item.get("friendly_name", item["name"])

            cols = st.columns([3, 3, 1])
            cols[0].markdown(
                f"**{display_name}{conf_badge}** &nbsp; £{float(item['price']):.2f}",
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

        # Discount reminder
        discount_parts = []
        if colleague_discount > 0:
            discount_parts.append(f"{colleague_discount:.0f}% colleague discount")
        if extra_discount > 0:
            discount_parts.append(f"{extra_discount:.0f}% extra discount")
        if discount_parts:
            st.info(f"🏷️ {' + '.join(discount_parts)} will be applied to the totals below.")

        st.subheader("Who paid today?")
        st.caption("Select the person who physically paid at the till — Splitwise will work out what everyone owes them.")
        payer = st.radio("Payer", PEOPLE, horizontal=True, label_visibility="collapsed")

        # Nudge to recalculate if payer changed after a previous calculation
        already_calculated = "final_totals" in st.session_state
        discount_changed = (
            already_calculated and (
                colleague_discount != st.session_state.get("calculated_colleague") or
                extra_discount     != st.session_state.get("calculated_extra")
            )
        )
        payer_changed = already_calculated and payer != st.session_state.get("calculated_payer")
        needs_recalc  = payer_changed or discount_changed

        if payer_changed:
            st.warning("⚠️ Payer has changed — hit Recalculate to update the totals.")
        if discount_changed:
            st.warning("⚠️ Discount has changed — hit Recalculate to update the totals.")

        btn_label = "🔄 Recalculate Split" if needs_recalc else "Calculate Split"
        if st.button(btn_label, type="primary", use_container_width=True):
            exact_totals = {p: 0.0 for p in PEOPLE}
            for item in st.session_state.receipt_items:
                item_id = item["id"]
                split   = st.session_state.assignments.get(item_id, [])
                if not split:
                    continue
                price = discounted_price(float(item["price"]), colleague_discount, extra_discount)
                share = price / len(split)
                for person in split:
                    exact_totals[person] += share
            final_totals = {p: round(v * 100) for p, v in exact_totals.items()}
            st.session_state.final_totals              = final_totals
            st.session_state.payer                     = payer
            st.session_state.calculated_payer          = payer
            st.session_state.calculated_colleague      = colleague_discount
            st.session_state.calculated_extra          = extra_discount
            st.session_state.splitwise_sent            = False
            st.session_state.balloons_shown            = False

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

            # Summary — sanity check before firing
            st.markdown(f"**💳 Paid by: {payer} (£{grand/100:.2f} total)**")
            rows = {}
            for person in PEOPLE:
                owed = f"£{final_totals[person] / 100:.2f}"
                if person == payer:
                    rows[person] = f"{owed} (paid — keeps this)"
                else:
                    rows[person] = f"{owed} (owes {payer})"
            for person, detail in rows.items():
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**{person}**")
                c2.markdown(detail)
            st.divider()

            if not st.session_state.get("splitwise_sent"):
                if st.button("➕ Create Splitwise Expense", type="primary", use_container_width=True):
                    with st.spinner("Sending to Splitwise..."):
                        try:
                            result   = create_splitwise_expense(expense_name, grand, payer, final_totals)
                            expenses = result.get("expenses", [])
                            errors   = result.get("errors", {})
                            if expenses and not errors:
                                st.session_state.splitwise_sent = True
                                st.rerun()
                            else:
                                st.error(f"Splitwise error: {result}")
                        except Exception as e:
                            st.error(f"Failed: {e}")
            else:
                st.success(f"✅ '{expense_name}' added to Splitwise! {payer} paid £{grand/100:.2f}.")
                if not st.session_state.get("balloons_shown"):
                    st.session_state.balloons_shown = True
                    st.balloons()
                st.divider()
                if st.button("🛒 Start a New Receipt", type="primary", use_container_width=True):
                    for key in list(st.session_state.keys()):
                        del st.session_state[key]
                    st.rerun()

        st.divider()
        if st.button("← Back to Split", use_container_width=True):
            st.session_state.step = 2
            st.rerun()

