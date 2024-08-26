import google.generativeai as genai
import PIL.Image
import os

genai.configure(api_key=os.environ["GOOGLE_API_KEY"])

model = genai.GenerativeModel("gemini-1.5-flash")
response = model.generate_content(["Tell me about this instrument"])
print(response.text)

      

