import os
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
import google.generativeai as genai
from pypdf import PdfReader

# --- 1. CONFIGURACIÓN INICIAL ---
load_dotenv()

supabase_url: str = os.environ.get("SUPABASE_URL")
supabase_key: str = os.environ.get("SUPABASE_KEY")
gemini_api_key = os.environ.get("GEMINI_API_KEY")

if not all([supabase_url, supabase_key, gemini_api_key]):
    print("Error: Faltan credenciales en el archivo .env")
    exit()

supabase: Client = create_client(supabase_url, supabase_key)
genai.configure(api_key=gemini_api_key)

# --- 2. EXTRACCIÓN ESTRUCTURADA CON GEMINI ---
def auditar_y_extraer_parametros(texto_documento):
    """Envía el texto a Gemini y exige una respuesta estructurada en JSON."""
    modelo = genai.GenerativeModel('gemini-2.5-flash')
    
    prompt_sistema = f"""
    Eres un auditor experto en contratación pública e infraestructura tecnológica.
    Analiza el siguiente extracto de un TDR u orden de compra.
    
    Debes auditar el texto y extraer los parámetros técnicos. 
    Tu respuesta debe ser ÚNICAMENTE un objeto JSON válido, sin texto adicional ni bloques de código markdown, con esta estructura exacta:
    {{
        "resumen_auditoria": "Tu análisis normativo (ambigüedades, plazos lógicos, garantías).",
        "tipo_equipo": "Ej. Servidores, UPS, Data Center, etc. (o 'No especificado')",
        "meses_garantia": (número entero, si no se especifica pon 0),
        "dias_frecuencia_mantenimiento": (número entero en días, ej. si es 1 vez al año pon 365. Si no se especifica pon 0)
    }}
    
    Texto a analizar:
    {texto_documento}
    """
    print("\n[Sistema] Analizando normativa y extrayendo parámetros técnicos con IA...")
    respuesta = modelo.generate_content(prompt_sistema)
    
    try:
        # Limpiamos posibles caracteres markdown de la respuesta de Gemini
        texto_limpio = respuesta.text.strip().replace("```json", "").replace("```", "")
        return json.loads(texto_limpio)
    except json.JSONDecodeError:
        print("Error al decodificar la respuesta de Gemini.")
        return None

# --- 3. LECTURA Y BASE DE DATOS ---
def extraer_texto_pdf(ruta_archivo):
    print(f"[Sistema] Leyendo documento: {ruta_archivo}...")
    try:
        reader = PdfReader(ruta_archivo)
        texto_completo = "".join(pagina.extract_text() + "\n" for pagina in reader.pages)
        return texto_completo
    except FileNotFoundError:
        print(f"Error: No se encontró el archivo '{ruta_archivo}'.")
        return None

def procesar_flujo_completo(ruta_pdf, num_proceso, proveedor, monto, fecha_texto):
    # 1. Leer PDF
    texto_contrato = extraer_texto_pdf(ruta_pdf)
    if not texto_contrato: return
    
    # 2. Auditar y extraer JSON
    datos_ia = auditar_y_extraer_parametros(texto_contrato)
    if not datos_ia: return
    
    print("\n================ RESUMEN DE AUDITORÍA ================")
    print(datos_ia["resumen_auditoria"])
    print("======================================================\n")
    
    # 3. Guardar Orden Principal
    fecha_suscripcion = datetime.strptime(fecha_texto, "%Y-%m-%d")
    
    datos_orden = {
        "numero_proceso_sercop": num_proceso,
        "objeto_contratacion": f"Vigencia Tecnológica - {datos_ia['tipo_equipo']}",
        "proveedor": proveedor,
        "monto": monto,
        "fecha_suscripcion": fecha_suscripcion.strftime("%Y-%m-%d")
    }
    
    try:
        print("[Sistema] Insertando orden en base de datos...")
        # Al insertar, Supabase devuelve los datos creados (incluyendo el ID generado)
        respuesta_orden = supabase.table("ordenes_compra").insert(datos_orden).execute()
        orden_id_generada = respuesta_orden.data[0]['id']
        
        # 4. Calcular Fechas y Guardar Vigencia Tecnológica
        dias_mantenimiento = datos_ia["dias_frecuencia_mantenimiento"]
        # Formateamos estrictamente la suma de fechas utilizando timedelta
        proximo_mantenimiento = fecha_suscripcion + timedelta(days=dias_mantenimiento)
        
        datos_vigencia = {
            "orden_id": orden_id_generada, # Vinculación relacional
            "tipo_equipo": datos_ia["tipo_equipo"],
            "meses_garantia": datos_ia["meses_garantia"],
            "frecuencia_mantenimiento_dias": dias_mantenimiento,
            "proximo_mantenimiento": proximo_mantenimiento.strftime("%Y-%m-%d"),
            "estado_cumplimiento": "Al día"
        }
        
        print("[Sistema] Programando cronograma de mantenimiento...")
        supabase.table("vigencia_tecnologica").insert(datos_vigencia).execute()
        
        print(f"¡Éxito! El proceso {num_proceso} y su cronograma se han registrado correctamente.")
        print(f"-> Próximo mantenimiento calculado para: {proximo_mantenimiento.strftime('%Y-%m-%d')}")
        
    except Exception as e:
        print(f"Error en la base de datos: {e}")

# --- 4. EJECUCIÓN ---
if __name__ == "__main__":
    archivo_prueba = "orden_prueba.pdf" 
    
    procesar_flujo_completo(
        ruta_pdf=archivo_prueba,
        num_proceso="SIE-002-2026", 
        proveedor="Tech Solutions S.A.",
        monto=15400.50,
        fecha_texto="2026-06-12" # <--- Nombre corregido
    )