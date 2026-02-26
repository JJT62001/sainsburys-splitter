import streamlit as st
from google import genai
from PIL import Image
import json
import random

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
    /* Person colour tokens */
    :root {
        --joe:  #22c55e;   /* green  */
        --nic:  #3b82f6;   /* blue   */
        --nat:  #a855f7;   /* purple */
        --shared: #f59e0b; /* amber  */
    }

    /* Row highlights */
    .row-joe    { background: rgba(34,197,94,0.10);  border-left: 4px solid var(--joe);  border-radius: 6px; padding: 6px 10px; margin-bottom: 6px; }
    .row-nic    { background: rgba(59,130,246,0.10); border-left: 4px solid var(--nic);  border-radius: 6px; padding: 6px 10px; margin-bottom: 6px; }
    .row-nat    { background: rgba(168,85,247,0.10); border-left: 4px solid var(--nat);  border-radius: 6px; padding: 6px 10px; margin-bottom: 6px; }
    .row-shared { background: rgba(245,158,11,0.10); border-left: 4px solid var(--shared); border-radius: 6px; padding: 6px 10px; margin-bottom: 6px; }
    .row-none   { background: rgba(100,100,100,0.05); border-left: 4px solid #ccc; border-radius: 6px; padding: 6px 10px; margin-bottom: 6px; }

    /* Confidence warning badge */
    .badge-low  { background:#fef3c7; color:#92400e; border:1px solid #fcd34d; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }
    .badge-ok   { background:#dcfce7; color:#166534; border:1px solid #86efac; border-radius:4px; padding:1px 6px; font-size:0.75rem; font-weight:600; }

    /* Totals bar */
    .totals-bar { display:flex; gap:16px; flex-wrap:wrap; margin: 8px 0; }
    .total-chip { border-radius:12px; padding:10px 20px; font-weight:700; font-size:1.1rem; flex:1; text-align:center; min-width:120px; }
    .chip-joe   { background:rgba(34,197,94,0.15);  border:2px solid var(--joe);  color:#15803d; }
    .chip-nic   { background:rgba(59,130,246,0.15); border:2px solid var(--nic);  color:#1d4ed8; }
    .chip-nat   { background:rgba(168,85,247,0.15); border:2px solid var(--nat);  color:#7e22ce; }
    .chip-grand { background:rgba(15,23,42,0.07);   border:2px solid #475569;     color:#1e293b; }

    div[data-testid="stMetric"] { background: transparent !important; }
</style>
""", unsafe_allow_html=True)

st.title("🛒 Joe, Nic & Nat's Sainsbury's Splitter")

PEOPLE = ["Joe", "Nic", "Nat"]
PERSON_CSS = {"Joe": "chip-joe", "Nic": "chip-nic", "Nat": "chip-nat"}

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


def row_css_class(split: list) -> str:
    if not split:
        return "row-none"
    if len(split) > 1:
        return "row-shared"
    return f"row-{split[0].lower()}"


def live_totals(receipt_items, assignments, apply_15, extra_discount):
    """Return per-person totals (in pence) from current multiselect state."""
    totals = {p: 0 for p in PEOPLE}
    for item, asgn in zip(receipt_items, assignments):
        if not asgn:
            continue
        price = discounted_price(float(item["price"]), apply_15, extra_discount)
        pennies = round(price * 100)
        share = pennies // len(asgn)
        for person in asgn:
            totals[person] += share
    return totals


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
            Extract items from this Sainsbury's receipt.
            Use the Nectar price if available.
            Ignore 'Total', 'Subtotal', 'Bag' and payment lines.

            For each item also add a "confidence" field:
              - 1.0 = you can clearly read the name and price
              - 0.5 = you had to guess the name or price (blurry, cut off, ambiguous)
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
        st.session_state.assignments = [[] for _ in range(n)]

    assignments = []

    for i, item in enumerate(st.session_state.receipt_items):
        row_class = row_css_class(st.session_state.assignments[i])
        st.markdown(f'<div class="{row_class}">', unsafe_allow_html=True)

        cols = st.columns([3, 3, 1])

        conf = float(item.get("confidence", 1.0))
        conf_badge = " ⚠️" if conf < 0.75 else ""
        cols[0].markdown(f"**{item['name']}{conf_badge}** &nbsp; £{float(item['price']):.2f}",
                         unsafe_allow_html=True)

        selected = cols[1].multiselect(
            "Who's in?",
            PEOPLE,
            default=st.session_state.assignments[i],
            key=f"split_{i}",
            label_visibility="collapsed"
        )

        # "All" quick-assign button
        if cols[2].button("All", key=f"all_{i}"):
            st.session_state.assignments[i] = PEOPLE[:]
            st.rerun()

        st.session_state.assignments[i] = selected
        assignments.append({"price": float(item["price"]), "split": selected})

        st.markdown('</div>', unsafe_allow_html=True)

    # ==============================
    # Live Running Totals
    # ==============================
    st.divider()
    st.subheader("💰 Live Totals")

    live = live_totals(st.session_state.receipt_items,
                       [st.session_state.assignments[i] for i in range(n)],
                       apply_15, extra_discount)
    grand_total = sum(live.values())

    totals_html = '<div class="totals-bar">'
    for person in PEOPLE:
        css = PERSON_CSS[person]
        totals_html += f'<div class="total-chip {css}">{person}<br>£{live[person]/100:.2f}</div>'
    totals_html += f'<div class="total-chip chip-grand">🧾 Grand Total<br>£{grand_total/100:.2f}</div>'
    totals_html += '</div>'
    st.markdown(totals_html, unsafe_allow_html=True)

    st.caption("Updates live as you assign items above. Grand total = sum of everyone's share (unassigned items excluded).")

    # ==============================
    # Final Calculation (with proper penny rounding)
    # ==============================
    st.divider()
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

        st.balloons()
        st.success("### 🎉 Final Totals")

        cols = st.columns(len(PEOPLE))
        for col, person in zip(cols, PEOPLE):
            col.metric(label=person, value=f"£{final_totals[person] / 100:.2f}")

        grand = sum(final_totals.values())
        st.metric(label="🧾 Grand Total (all splits)", value=f"£{grand / 100:.2f}")
        st.caption("Add this to Splitwise and you're done! 🙌")
