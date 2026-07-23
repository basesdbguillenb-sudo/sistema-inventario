import os
from dotenv import load_dotenv
import google.generativeai as genai

# Cargar tu clave desde el .env
load_dotenv()
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))

print("Consultando a Google los modelos disponibles para tu API Key...\n")

try:
    for m in genai.list_models():
        # Filtramos solo los modelos que sirven para generar texto
        if 'generateContent' in m.supported_generation_methods:
            print(f"✅ Nombre exacto a usar: {m.name}")
except Exception as e:
    print(f"Error de conexión: {e}")