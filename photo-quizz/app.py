import os
from pathlib import Path
import pandas as pd
from pydantic import BaseModel, Field
from PIL import Image
from google import genai
from google.genai import types

# 1. Define the Structured Output Schema
class MCQ(BaseModel):
    source_image: str = Field(description="Filename of the textbook image")
    question: str = Field(description="The multiple choice question")
    option_a: str = Field(description="Option A")
    option_b: str = Field(description="Option B")
    option_c: str = Field(description="Option C")
    option_d: str = Field(description="Option D")
    correct_answer: str = Field(description="Correct option label (A, B, C, or D)")
    explanation: str = Field(description="Brief explanation of why the answer is correct")

class MCQList(BaseModel):
    questions: list[MCQ]

def generate_mcqs_from_folder(
    data_dir: str = "data", 
    output_excel: str = "textbook_mcqs.xlsx",
    questions_per_page: int = 5
):
    client = genai.Client()
    image_extensions = {".jpg", ".jpeg", ".png", ".webp"}
    
    data_path = Path(data_dir)
    image_files = [f for f in data_path.iterdir() if f.suffix.lower() in image_extensions]

    if not image_files:
        print(f"No images found in '{data_dir}'. Place textbook photos there first.")
        return

    all_mcqs = []

    for img_path in image_files:
        print(f"Processing: {img_path.name}...")
        try:
            image = Image.open(img_path)
            
            prompt = (
                f"You are an educational assistant. Read the content of this textbook page "
                f"and create {questions_per_page} high-quality multiple choice questions (MCQs). "
                f"Each question must have 4 options (A, B, C, D) and exactly one correct answer. "
                f"Set 'source_image' to '{img_path.name}'."
            )

            # Updated to gemini-3.6-flash
            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[prompt, image],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=MCQList,
                    temperature=0.2,
                ),
            )

            # Parse structured JSON
            parsed_data = MCQList.model_validate_json(response.text)
            all_mcqs.extend([mcq.model_dump() for mcq in parsed_data.questions])

        except Exception as e:
            print(f"Error processing {img_path.name}: {e}")

    # 2. Export to Excel
    if all_mcqs:
        df = pd.DataFrame(all_mcqs)
        
        df.rename(columns={
            "source_image": "Source Page",
            "question": "Question",
            "option_a": "Option A",
            "option_b": "Option B",
            "option_c": "Option C",
            "option_d": "Option D",
            "correct_answer": "Correct Option",
            "explanation": "Explanation"
        }, inplace=True)

        df.to_excel(output_excel, index=False, engine="openpyxl")
        print(f"\nDone! Generated {len(df)} MCQs and saved to '{output_excel}'.")
    else:
        print("No MCQs could be generated.")

if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    generate_mcqs_from_folder(data_dir="data", output_excel="output_mcqs.xlsx")
