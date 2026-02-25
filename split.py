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
st.title("🛒 Joe, Nic & Nat's Sainsbury's Splitter")

# ==============================
# Reset / New Receipt
# ==============================
if st.button("🔄 Start New Receipt"):
    for key in list(st.session_state.keys()):
        del st.session_state[key]
    st.rerun()

uploaded_file = st.file_uploader(
    "Upload Receipt Photo",
    type=["jpg", "jpeg", "png"]
)

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
            Ignore 'Total' lines.

            Return ONLY a valid JSON list in this format:
            [
              {"name": "Item Name", "price": 1.50}
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

                raw_text = response.text

                if not raw_text:
                    st.error("No response received from Gemini.")
                    st.stop()

                raw_text = raw_text.replace("```json", "").replace("```", "").strip()

                start_index = raw_text.find("[")
                end_index = raw_text.rfind("]") + 1

                if start_index == -1 or end_index == 0:
                    st.error("Couldn't find valid JSON list in AI response.")
                    st.write(raw_text)
                    st.stop()

                clean_json = raw_text[start_index:end_index]
                st.session_state.receipt_items = json.loads(clean_json)

                st.success("Receipt successfully analyzed!")

            except Exception as e:
                st.error(f"Error analyzing receipt: {e}")

# ==============================
# Edit + Add Items Section
# ==============================
if "receipt_items" in st.session_state:

    st.divider()
    st.subheader("Review & Edit Items")

    updated_items = []

    for i, item in enumerate(st.session_state.receipt_items):
        cols = st.columns([3, 2, 1])

        name = cols[0].text_input(
            "Item Name",
            value=item["name"],
            key=f"name_{i}"
        )

        price = cols[1].number_input(
            "Price (£)",
            value=float(item["price"]),
            step=0.01,
            key=f"price_{i}"
        )

        delete = cols[2].button("❌", key=f"delete_{i}")

        if not delete:
            updated_items.append({"name": name, "price": price})

    st.session_state.receipt_items = updated_items

    # ==============================
    # Add New Item
    # ==============================
    st.markdown("### ➕ Add Missing Item")

    new_name = st.text_input("New Item Name")
    new_price = st.number_input("New Item Price (£)", min_value=0.0, step=0.01)

    if st.button("Add Item"):
        if new_name:
            st.session_state.receipt_items.append({
                "name": new_name,
                "price": new_price
            })
            st.rerun()
        else:
            st.warning("Please enter an item name.")

    # ==============================
    # Discount Options
    # ==============================
    st.divider()
    st.subheader("Discount Settings")

    col1, col2 = st.columns(2)

    apply_15 = col1.checkbox(
        "Apply 15% Colleague Discount",
        value=True
    )

    extra_discount = col2.number_input(
        "Additional Discount (%)",
        min_value=0.0,
        max_value=100.0,
        step=0.5,
        value=0.0
    )

    st.info(f"""
    Active Discounts:
    • 15% Colleague Discount: {"ON" if apply_15 else "OFF"}
    • Extra Discount: {extra_discount}%
    """)

    # ==============================
    # Splitting Section
    # ==============================
    st.divider()
    st.subheader("Split the Items")

    assignments = []

    for i, item in enumerate(st.session_state.receipt_items):
        cols = st.columns([3, 2])

        cols[0].write(
            f"**{item['name']}** (£{float(item['price']):.2f})"
        )

        selected = cols[1].multiselect(
            "Who's in?",
            ["Joe", "Nic", "Nat"],
            key=f"split_{i}"
        )

        assignments.append({
            "price": float(item["price"]),
            "split": selected
        })

    # ==============================
    # Final Calculation
    # ==============================
    if st.button("Finalise Split"):

        totals = {"Joe": 0, "Nic": 0, "Nat": 0}

        for item in assignments:
            if not item["split"]:
                continue

            price = item["price"]

            # Apply 15% discount
            if apply_15:
                price *= 0.85

            # Apply additional discount
            if extra_discount > 0:
                price *= (1 - extra_discount / 100)

            pennies = round(price * 100)

            share = pennies // len(item["split"])
            remainder = pennies % len(item["split"])

            for person in item["split"]:
                totals[person] += share

            if remainder > 0:
                for winner in random.sample(item["split"], k=remainder):
                    totals[winner] += 1

        st.balloons()
        st.success("### Final Totals")

        for person, total in totals.items():
            st.metric(label=person, value=f"£{total / 100:.2f}")