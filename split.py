import streamlit as st
from google import genai
from PIL import Image, ImageOps, ImageFilter, ImageEnhance
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

    div[data-testid="stMetric"] { background: transparent !important; }

    /* Person toggle buttons */
    div[data-testid="stHorizontalBlock"] button[kind="secondary"].toggle-on {
        background-color: #16a34a !important;
        color: white !important;
        border-color: #16a34a !important;
    }
    .person-on  { display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700; font-size:0.9rem; cursor:pointer; background:#16a34a; color:white;  border:2px solid #16a34a; margin:2px; }
    .person-off { display:inline-block; padding:4px 14px; border-radius:20px; font-weight:700; font-size:0.9rem; cursor:pointer; background:transparent; color:#94a3b8; border:2px solid #334155; margin:2px; }
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
# Image Preprocessing
# ==============================
def preprocess_receipt(img: Image.Image) -> Image.Image:
    try:
        img = ImageOps.grayscale(img)
        min_height = 2000
        if img.height < min_height:
            scale = min_height / img.height
            img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
        img.thumbnail((3000, 3000), Image.LANCZOS)
        img = ImageOps.autocontrast(img, cutoff=2)
        img = img.filter(ImageFilter.SHARPEN)
        img = img.filter(ImageFilter.SHARPEN)
        return img.convert("RGB")
    except Exception:
        return img.convert("RGB") if img.mode != "RGB" else img

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
if "mode" not in st.session_state:
    st.session_state.mode = "receipt"

# ==============================
# Header + Mode Switcher
# ==============================
st.title("🛒 Joe, Nic & Nat's Splitter")

col_a, col_b, _ = st.columns([1, 1, 3])
if col_a.button(
    "🛒 Receipt Split",
    type="primary" if st.session_state.mode == "receipt" else "secondary",
    use_container_width=True,
):
    st.session_state.mode = "receipt"
    st.rerun()
if col_b.button(
    "➕ Quick Expense",
    type="primary" if st.session_state.mode == "quick" else "secondary",
    use_container_width=True,
):
    st.session_state.mode = "quick"
    st.rerun()

st.divider()

# ==============================
# QUICK EXPENSE MODE
# ==============================
if st.session_state.mode == "quick":

    st.subheader("➕ Quick Expense")
    st.caption("Add a Splitwise expense directly — no receipt needed.")

    if "qe_sent" not in st.session_state:
        st.session_state.qe_sent = False

    if not st.session_state.qe_sent:

        qe_desc  = st.text_input("Description", placeholder="e.g. Pizza, Netflix, Petrol...")
        qe_total = st.number_input("Total Amount (£)", min_value=0.01, step=0.01, value=10.00)

        st.markdown("**Who's splitting this?**")
        st.caption("Everyone is in by default — deselect anyone not involved.")

        # Toggle state for quick expense
        if "qe_toggles" not in st.session_state:
            st.session_state.qe_toggles = {p: True for p in PEOPLE}

        tcols = st.columns(len(PEOPLE))
        for i, person in enumerate(PEOPLE):
            is_on    = st.session_state.qe_toggles[person]
            label    = f"{'✓ ' if is_on else ''}{person}"
            btn_type = "primary" if is_on else "secondary"
            if tcols[i].button(label, key=f"qe_tog_{person}", type=btn_type, use_container_width=True):
                st.session_state.qe_toggles[person] = not is_on
                st.rerun()

        splitting = [p for p in PEOPLE if st.session_state.qe_toggles[p]]

        if not splitting:
            st.warning("⚠️ Nobody is selected — add at least one person.")
        else:
            # Live preview of shares
            share_pennies = round(qe_total * 100) // len(splitting)
            remainder     = round(qe_total * 100) % len(splitting)
            st.caption(f"Each person pays roughly £{share_pennies / 100:.2f}" +
                       (f" (±1p rounding)" if remainder else ""))

        st.markdown("**Who paid?**")
        qe_payer = st.radio("Payer", PEOPLE, horizontal=True, label_visibility="collapsed")

        st.divider()

        # Summary before sending
        if splitting:
            st.markdown(f"**💳 Paid by: {qe_payer} (£{qe_total:.2f} total)**")
            total_pennies = round(qe_total * 100)
            share         = total_pennies // len(splitting)
            remainder     = total_pennies % len(splitting)
            shares        = {p: share for p in splitting}
            # Distribute remainder penny by penny to first person(s)
            for p in splitting[:remainder]:
                shares[p] += 1
            # Zero out anyone not splitting
            for p in PEOPLE:
                if p not in splitting:
                    shares[p] = 0

            for person in PEOPLE:
                c1, c2 = st.columns([2, 3])
                c1.markdown(f"**{person}**")
                if person not in splitting:
                    c2.markdown("*not splitting*")
                elif person == qe_payer:
                    c2.markdown(f"£{shares[person] / 100:.2f} (paid — keeps this)")
                else:
                    c2.markdown(f"£{shares[person] / 100:.2f} (owes {qe_payer})")

            st.divider()

            if st.button("➕ Create Splitwise Expense", type="primary", use_container_width=True):
                if not qe_desc.strip():
                    st.warning("Please enter a description.")
                else:
                    with st.spinner("Sending to Splitwise..."):
                        try:
                            result   = create_splitwise_expense(qe_desc, total_pennies, qe_payer, shares)
                            expenses = result.get("expenses", [])
                            errors   = result.get("errors", {})
                            if expenses and not errors:
                                st.session_state.qe_sent      = True
                                st.session_state.qe_last_desc = qe_desc
                                st.session_state.qe_last_total = qe_total
                                st.session_state.qe_last_payer = qe_payer
                                st.rerun()
                            else:
                                st.error(f"Splitwise error: {result}")
                        except Exception as e:
                            st.error(f"Failed: {e}")
    else:
        st.success(
            f"✅ '{st.session_state.qe_last_desc}' added to Splitwise! "
            f"{st.session_state.qe_last_payer} paid £{st.session_state.qe_last_total:.2f}."
        )
        st.balloons()
        if st.button("➕ Add Another Expense", type="primary", use_container_width=True):
            st.session_state.qe_sent    = False
            st.session_state.qe_toggles = {p: True for p in PEOPLE}
            st.rerun()

# ==============================
# RECEIPT SPLIT MODE
# ==============================
else:

    # ---- STEP 0: Upload & Scan ----
    if st.session_state.step == 0:
        uploaded_file = st.file_uploader("Upload Receipt Photo", type=["jpg", "jpeg", "png"])

        if uploaded_file:
            img = Image.open(uploaded_file)
            processed_img = preprocess_receipt(img)

            col1, col2 = st.columns(2)
            col1.image(img, caption="📷 Original", use_container_width=True)
            col2.image(processed_img, caption="✨ Processed (what Gemini sees)", use_container_width=True)

            st.markdown("#### Receipt uploaded ✓")
            st.caption("The processed version is greyscaled, sharpened and contrast-enhanced — this is what Gemini will read.")
            if st.button("🔍 Analyse Receipt", type="primary", use_container_width=True):
                try:
                    with st.spinner("Reading receipt text..."):
                        ocr_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[
                                """Transcribe every line of this receipt exactly as printed.
Preserve the original layout — one line per item.
Include ALL lines: item names, prices, Nectar savings, ITEM CANCELLED lines, totals, everything.
Do not interpret, skip, or summarise anything. Just output the raw text.""",
                                processed_img,
                            ],
                            config={"temperature": 0.0}
                        )
                        raw_text = ocr_response.text.strip()

                    with st.spinner("Interpreting items..."):
                        parse_prompt = f"""You are parsing a Sainsbury's receipt. Here is the exact text extracted from it:

<receipt>
{raw_text}
</receipt>

Your job is to return the FINAL price each item cost after all discounts.

RULE 1 — Nectar / loyalty savings:
A "Nectar Price Saving" or "Nectar Saver" line with a NEGATIVE value immediately follows the item it applies to.
Subtract it from that item. Never include the saving line as its own item.

Example:
  TTD EASY PEEL 600G    2.50
  Nectar Price Saving  -0.75
Output: name="TTD EASY PEEL 600G", price=1.75

RULE 2 — Cancelled items:
Three lines: item + positive price, then "ITEM CANCELLED", then same item + negative price.
Omit all three lines entirely.

Example:
  JS SOURDOUGH BAG    2.00
  ITEM CANCELLED
  JS SOURDOUGH BAG   -2.00
Output: omit entirely

RULE 3 — Multibuy / promotional discounts:
Sometimes a discount line appears after a group of items, e.g. "Partbaked 2 for 3" with a negative value like -1.00.
This means the discount applies across those items. Deduct it from the last item before the discount line.

Example:
  TTD 4 BAKE WHTE ROLL   2.00
  TTD 4 BAKE WHTE ROLL   2.00
  Partbaked 2 for 3      -1.00
Output: first roll price=2.00, second roll price=1.00

RULE 4 — Duplicate items:
If the same item appears multiple times at the same price, include each as a separate entry.

RULE 5 — Ignore these lines entirely:
Balance Due, Total, Subtotal, Colleague Discount, card payment, change, cashier messages,
and age verification lines (e.g. "THINK 25 Cashier Confirmed Over 18").

RULE 6 — Lines starting with *:
These are age-restricted items (alcohol, medication) — include them as normal, strip the * from the name.

For each item return:
- name: exact receipt text
- friendly_name: human-readable English version (decode abbreviations, e.g. "JS CHK BRST FIL 320G" -> "Chicken Breast Fillets 320g")
- price: final price after all applicable discounts
- confidence: 1.0 if certain, 0.5 if unsure about name or price, 0.0 if very uncertain
"""
                        parse_response = client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=[parse_prompt],
                            config={
                                "response_mime_type": "application/json",
                                "response_schema": list[ReceiptItem],
                                "temperature": 0.0,
                            }
                        )
                        items = json.loads(parse_response.text)
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

    # ---- STEPS 1-3 ----
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

            for item in st.session_state.receipt_items:
                toggle_key = f"toggle_{item['id']}"
                if toggle_key not in st.session_state:
                    st.session_state[toggle_key] = {p: True for p in PEOPLE}

            unassigned = sum(
                1 for item in st.session_state.receipt_items
                if not any(st.session_state.get(f"toggle_{item['id']}", {p: True for p in PEOPLE}).values())
            )
            if unassigned > 0:
                st.warning(f"⚠️ {unassigned} item(s) have nobody assigned — they won't be included in the split.")

            @st.fragment
            def render_item_toggle(item):
                item_id       = item["id"]
                conf          = float(item.get("confidence", 1.0))
                cleared_items = st.session_state.get("cleared_items", set())
                conf_badge    = " ⚠️" if conf < 0.75 and item_id not in cleared_items else ""
                display_name  = item.get("friendly_name", item["name"])
                toggle_key    = f"toggle_{item_id}"

                cols = st.columns([3, 1, 1, 1])
                cols[0].markdown(
                    f"**{display_name}{conf_badge}** &nbsp; £{float(item['price']):.2f}",
                    unsafe_allow_html=True
                )
                for i, person in enumerate(PEOPLE):
                    is_on    = st.session_state[toggle_key][person]
                    label    = f"{'✓ ' if is_on else ''}{person}"
                    btn_type = "primary" if is_on else "secondary"
                    if cols[i + 1].button(label, key=f"tog_{item_id}_{person}", type=btn_type, use_container_width=True):
                        st.session_state[toggle_key][person] = not is_on
                        st.session_state.assignments[item_id] = [
                            p for p in PEOPLE if st.session_state[toggle_key][p]
                        ]
                        st.rerun()
                st.session_state.assignments[item_id] = [
                    p for p in PEOPLE if st.session_state[toggle_key][p]
                ]

            for item in st.session_state.receipt_items:
                render_item_toggle(item)

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
                st.session_state.final_totals         = final_totals
                st.session_state.payer                = payer
                st.session_state.calculated_payer     = payer
                st.session_state.calculated_colleague = colleague_discount
                st.session_state.calculated_extra     = extra_discount
                st.session_state.splitwise_sent       = False
                st.session_state.balloons_shown       = False

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

                st.markdown(f"**💳 Paid by: {payer} (£{grand/100:.2f} total)**")
                for person in PEOPLE:
                    owed = f"£{final_totals[person] / 100:.2f}"
                    detail = f"{owed} (paid — keeps this)" if person == payer else f"{owed} (owes {payer})"
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
