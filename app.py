import os
import re
import json
import tempfile
import threading
import time
from datetime import datetime
import pandas as pd
import gradio as gr
from fpdf import FPDF
from pypdf import PdfReader
from dotenv import load_dotenv
import concurrent.futures

from supabase import create_client, Client, ClientOptions
from google import genai

load_dotenv()

supabase_url = os.environ.get("SUPABASE_URL", "").strip()
supabase_key = os.environ.get("SUPABASE_KEY", "").strip()
gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

if supabase_url and supabase_key:
    opciones = ClientOptions(postgrest_client_timeout=15, schema="public")
    supabase: Client = create_client(supabase_url, supabase_key, options=opciones)
else:
    print("⚠️ ADVERTENCIA: Faltan credenciales de Supabase en las variables de entorno.")
    supabase = None

try:
    if gemini_key:
        gemini_client = genai.Client(api_key=gemini_key)
    else:
        gemini_client = None
except Exception as e:
    print(f"⚠️ ADVERTENCIA: Error configurando Gemini: {e}")
    gemini_client = None

def llamar_gemini_con_reintentos(prompt, max_reintentos=4):
    for intento in range(max_reintentos):
        try:
            return gemini_client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        except Exception as e:
            error_str = str(e)
            if '503' in error_str or '429' in error_str or 'UNAVAILABLE' in error_str:
                if intento < max_reintentos - 1:
                    tiempo_espera = 2 ** intento
                    time.sleep(tiempo_espera)
                else:
                    raise Exception("Los servidores de IA de Google están experimentando demasiada demanda. Por favor, intenta de nuevo en un par de minutos.")
            else:
                raise e

def obtener_todos_los_registros(tabla):
    datos = []
    rango_inicio = 0
    rango_fin = 999
    while True:
        res = supabase.table(tabla).select("*").range(rango_inicio, rango_fin).execute()
        if not res.data:
            break
        datos.extend(res.data)
        if len(res.data) < 1000:
            break
        rango_inicio += 1000
        rango_fin += 1000
    return datos

def respaldar_base_datos():
    tablas = ["usuarios_sistema", "auditoria_sistema", "equipos", "funcionarios", "ordenes_compra", "mantenimientos"]
    carpeta_respaldos = os.path.join(tempfile.gettempdir(), "respaldos")
    os.makedirs(carpeta_respaldos, exist_ok=True)
    
    fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    ruta_archivo = os.path.join(carpeta_respaldos, f"respaldo_bd_{fecha_str}.json")
    
    datos_completos = {}
    try:
        for tabla in tablas:
            datos_completos[tabla] = obtener_todos_los_registros(tabla)
            
        with open(ruta_archivo, "w", encoding="utf-8") as f:
            json.dump(datos_completos, f, indent=4, ensure_ascii=False)
        return ruta_archivo
    except Exception as e:
        return None

def generar_respaldo_manual(request: gr.Request):
    usuario = request.username if request else "Sistema"
    ruta = respaldar_base_datos()
    if ruta:
        registrar_auditoria(usuario, "Generó descarga manual del respaldo de la base de datos.")
        return gr.update(value=ruta, visible=True), "✅ Respaldo generado y listo para descargar."
    return gr.update(visible=False, value=None), "❌ Error al generar el respaldo."

def restaurar_base_datos(archivo_json, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not archivo_json:
        return "⚠️ Debes seleccionar un archivo JSON de respaldo."
    
    try:
        ruta_archivo = archivo_json.name if hasattr(archivo_json, 'name') else archivo_json
        with open(ruta_archivo, "r", encoding="utf-8") as f:
            datos_respaldo = json.load(f)
            
        tablas_orden = ["usuarios_sistema", "funcionarios", "ordenes_compra", "equipos", "mantenimientos", "auditoria_sistema"]
        total_restaurados = 0
        
        for tabla in tablas_orden:
            if tabla in datos_respaldo and datos_respaldo[tabla]:
                registros = datos_respaldo[tabla]
                for i in range(0, len(registros), 500):
                    bloque = registros[i:i + 500]
                    supabase.table(tabla).upsert(bloque).execute()
                    total_restaurados += len(bloque)
                    
        registrar_auditoria(usuario, f"Restauró la base de datos desde archivo de respaldo ({total_restaurados} registros).")
        return f"✅ Base de datos restaurada con éxito. Se procesaron {total_restaurados} registros."
    except Exception as e:
        return f"❌ Error al restaurar la base de datos: {e}"

def verificar_credenciales(usuario, clave):
    try:
        res = supabase.table("usuarios_sistema").select("*").eq("usuario", usuario).eq("clave", clave).execute()
        return len(res.data) > 0
    except Exception: return False

def registrar_auditoria(usuario, accion):
    try: supabase.table("auditoria_sistema").insert({"usuario": usuario, "accion": accion}).execute()
    except Exception: pass

def cargar_datos_auditoria():
    try:
        res = supabase.table("auditoria_sistema").select("*").order("fecha_registro", desc=True).execute()
        datos = [[item.get("fecha_registro", "")[:19].replace("T", " "), item.get("usuario", "Desconocido"), item.get("accion", "")] for item in res.data]
        return datos if datos else [["-", "-", "Sin movimientos registrados"]]
    except Exception as e: return [["Error", "-", str(e)]]

def cargar_usuarios_sistema():
    try:
        res = supabase.table("usuarios_sistema").select("usuario, rol").execute()
        datos = [[item.get("usuario", ""), item.get("rol", "admin")] for item in res.data]
        return datos if datos else [["-", "Sin usuarios"]]
    except Exception as e: return [["Error de conexión", str(e)]]

def obtener_usuarios_lista():
    try:
        res = supabase.table("usuarios_sistema").select("usuario, rol").execute()
        lista = [f"{item['usuario']} - {item['rol']}" for item in res.data]
        return gr.update(choices=lista, value=None)
    except Exception: return gr.update(choices=[], value=None)

def gestionar_usuario_admin(usuario_db, nueva_clave, rol_db, request: gr.Request):
    usuario_admin = request.username if request else "Sistema"
    if not usuario_db: return "⚠️ Debes ingresar el nombre de Usuario.", cargar_usuarios_sistema(), obtener_usuarios_lista()
    try:
        res = supabase.table("usuarios_sistema").select("*").eq("usuario", usuario_db).execute()
        if res.data:
            if not nueva_clave:
                supabase.table("usuarios_sistema").update({"rol": rol_db}).eq("usuario", usuario_db).execute()
                registrar_auditoria(usuario_admin, f"Actualizó rol del usuario: {usuario_db}.")
                msg = f"✅ Rol actualizado."
            else:
                supabase.table("usuarios_sistema").update({"clave": nueva_clave, "rol": rol_db}).eq("usuario", usuario_db).execute()
                registrar_auditoria(usuario_admin, f"Actualizó contraseña y rol del usuario: {usuario_db}.")
                msg = f"✅ Credenciales actualizadas."
        else:
            if not nueva_clave: return "⚠️ Para un usuario nuevo, debes asignar contraseña.", cargar_usuarios_sistema(), obtener_usuarios_lista()
            supabase.table("usuarios_sistema").insert({"usuario": usuario_db, "clave": nueva_clave, "rol": rol_db}).execute()
            registrar_auditoria(usuario_admin, f"Creó un nuevo usuario: {usuario_db}.")
            msg = f"✅ Nuevo usuario creado."
        return msg, cargar_usuarios_sistema(), obtener_usuarios_lista()
    except Exception as e: return f"❌ Error: {e}", cargar_usuarios_sistema(), obtener_usuarios_lista()

def analizar_fase_precontractual(archivos_principales, archivos_referencia, enlaces_input):
    if not archivos_principales: return "⚠️ Error: Anexa documentos."
    texto_contexto = "=== DOCUMENTOS PRINCIPALES ===\n"
    for archivo in archivos_principales:
        reader = PdfReader(archivo)
        for pagina in reader.pages: texto_contexto += pagina.extract_text() + "\n"
    if archivos_referencia:
        texto_contexto += "\n=== REFERENCIAS ===\n"
        for archivo in archivos_referencia:
            reader = PdfReader(archivo)
            for pagina in reader.pages: texto_contexto += pagina.extract_text() + "\n"
    try:
        prompt = f"Audita la fase precontractual (LOSNCP).\nCONTEXTO: {texto_contexto}\nENLACES: {enlaces_input}"
        respuesta = llamar_gemini_con_reintentos(prompt)
        return respuesta.text
    except Exception as e: return f"❌ Error de IA: {e}"

def cargar_datos_inventario(termino_busqueda=""):
    try:
        respuesta = supabase.table("equipos").select(
            "id, tipo_equipo, marca, modelo, numero_serie, estado, observaciones, funcionarios(nombres_completos, departamento), ordenes_compra(numero_proceso_sercop, numero_orden_compra, razon_social_proveedor, nombre_comercial)"
        ).execute()
        datos = []
        for item in respuesta.data:
            equipo = item.get("tipo_equipo", "")
            marca_modelo = f"{item.get('marca', '')} / {item.get('modelo', '')}"
            serie = item.get("numero_serie", "")
            estado = item.get("estado", "")
            observaciones = item.get("observaciones", "") or ""
            
            func = item.get("funcionarios")
            if isinstance(func, list) and func: func = func[0]
            elif not isinstance(func, dict): func = {}
            
            custodio = func.get("nombres_completos") if func else "Sin Asignar"
            departamento = func.get("departamento") if func else "-"
                
            ord_data = item.get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            elif not isinstance(ord_data, dict): ord_data = {}
            
            num_ce = ord_data.get("numero_orden_compra", "Sin CE") if ord_data else "Sin Orden"
            num_proceso = ord_data.get("numero_proceso_sercop", "-") if ord_data else "-"
            proveedor = ord_data.get("razon_social_proveedor", "No registrado") if ord_data else "-"
            nombre_comercial = ord_data.get("nombre_comercial", "") if ord_data else "-"
            
            fila = [equipo, marca_modelo, serie, custodio, departamento, estado, num_ce, num_proceso, proveedor, nombre_comercial, observaciones]
            if termino_busqueda and termino_busqueda.lower() not in " ".join([str(x) for x in fila]).lower():
                continue
            datos.append(fila)
            
        return datos if datos else [["-", "-", "Sin resultados", "-", "-", "-", "-", "-", "-", "-", "-"]]
    except Exception as e:
        return [["Error", "de", "conexión", "-", "-", "-", "-", "-", "-", "-", str(e)]]

def cargar_listado_funcionarios():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos, cargo, departamento").order("nombres_completos").execute()
        datos = [[item.get("cedula", ""), item.get("nombres_completos", ""), item.get("cargo", ""), item.get("departamento", "")] for item in res.data]
        return gr.update(value=datos if datos else [["-", "Sin registros", "-", "-"]])
    except Exception as e: return gr.update(value=[["Error", str(e), "-", "-"]])

def obtener_series_disponibles():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").is_("funcionario_id", "null").execute()
        series = [f"{item['numero_serie']} - {item.get('tipo_equipo', '')} {item.get('marca', '')}" for item in res.data]
        return gr.update(choices=series, value=None)
    except Exception: return gr.update(choices=[], value=None)

def obtener_todas_las_series():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo").execute()
        series = [f"{item['numero_serie']} - {item.get('tipo_equipo', '')}" for item in res.data]
        return gr.update(choices=series, value=None)
    except Exception: return gr.update(choices=[], value=None)

def obtener_series_asignadas():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, funcionario_id, funcionarios(nombres_completos)").not_("funcionario_id", "is", "null").execute()
        series = []
        for item in res.data:
            func = item.get("funcionarios")
            if isinstance(func, list) and func: func = func[0]
            elif not isinstance(func, dict): func = {}
            nombre = func.get("nombres_completos", "Desconocido") if func else "Desconocido"
            series.append(f"{item['numero_serie']} - {item.get('tipo_equipo', '')} ({nombre})")
        return gr.update(choices=series, value=None)
    except Exception: return gr.update(choices=[], value=None)

def obtener_funcionarios():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos").execute()
        lista = [f"{item['nombres_completos']} - {item['cedula']}" for item in res.data]
        return gr.update(choices=lista, value=None)
    except Exception: return gr.update(choices=[], value=None)

def realizar_busqueda(termino): 
    return cargar_datos_inventario(termino)

def obtener_funcionarios_mantenimiento():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos").execute()
        lista = [f"{item['nombres_completos']} - {item['cedula']}" for item in res.data]
        lista.insert(0, "Sin Asignar - BODEGA")
        return gr.update(choices=lista, value=None)
    except Exception: return gr.update(choices=[], value=None)

def cargar_equipos_de_funcionario(funcionario_combo):
    try:
        if not funcionario_combo: return gr.update(choices=[], value=[]), ""
        combo_str = str(funcionario_combo).strip()
        if combo_str == "Sin Asignar - BODEGA":
            res_eq = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").is_("funcionario_id", "null").execute()
            equipos = [f"{eq['numero_serie']} - {eq.get('tipo_equipo','')} {eq.get('marca','')}" for eq in res_eq.data]
            return gr.update(choices=equipos, value=[]), "Administrador de Bodega"

        cedula = combo_str.split(" - ")[-1].strip()
        res_func = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cedula).execute()
        if not res_func.data: return gr.update(choices=[], value=[]), ""
        
        func_id = res_func.data[0]['id']
        nombre_func = res_func.data[0]['nombres_completos']
        res_eq = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").eq("funcionario_id", func_id).execute()
        equipos = [f"{eq['numero_serie']} - {eq.get('tipo_equipo','')} {eq.get('marca','')}" for eq in res_eq.data]
        return gr.update(choices=equipos, value=[]), nombre_func
    except Exception: return gr.update(choices=[], value=[]), ""

def obtener_series_de_funcionario(funcionario_combo):
    try:
        if not funcionario_combo: return []
        combo_str = str(funcionario_combo).strip()
        if combo_str == "Sin Asignar - BODEGA":
            return [eq.get("numero_serie") for eq in supabase.table("equipos").select("numero_serie").is_("funcionario_id", "null").execute().data if eq.get("numero_serie")]
            
        cedula = combo_str.split(" - ")[-1].strip()
        res_func = supabase.table("funcionarios").select("id").eq("cedula", cedula).execute()
        if not res_func.data: return []
        return [eq.get("numero_serie") for eq in supabase.table("equipos").select("numero_serie").eq("funcionario_id", res_func.data[0]['id']).execute().data if eq.get("numero_serie")]
    except Exception: return []

def cargar_historial_por_funcionario(funcionario_combo):
    try:
        if not funcionario_combo: return [["-", "-", "-", "-", "-", "-", "-"]]
        series = obtener_series_de_funcionario(funcionario_combo)
        if not series: return [["-", "Este funcionario no tiene equipos", "-", "-", "-", "-", "-"]]
        
        res = supabase.table("mantenimientos").select("*").in_("numero_serie", series).order("id", desc=True).execute()
        datos = []
        for item in res.data:
            num = item.get("numero_informe")
            eq_str = item.get("numero_serie", "")
            if num:
                sg = [g.get("numero_serie", "") for g in supabase.table("mantenimientos").select("numero_serie").eq("numero_informe", num).execute().data if g.get("numero_serie")]
                if sg: eq_str = ", ".join(sorted(set(sg)))
            datos.append([item.get("fecha", ""), item.get("tipo_mantenimiento", ""), item.get("tecnico_proveedor", ""), eq_str, item.get("descripcion", ""), f"${item.get('costo', 0.0)}", item.get("proximo_mantenimiento", "")])
        return datos if datos else [["-", "Sin historial de mantenimientos", "-", "-", "-", "-", "-"]]
    except Exception as e: return [["Error", str(e), "-", "-", "-", "-", "-"]]

def obtener_mantenimientos_lista_por_funcionario(funcionario_combo):
    try:
        if not funcionario_combo: return gr.update(choices=[], value=None)
        series = obtener_series_de_funcionario(funcionario_combo)
        if not series: return gr.update(choices=[], value=None)
        
        res = supabase.table("mantenimientos").select("id, fecha, tipo_mantenimiento, numero_informe").in_("numero_serie", series).order("id", desc=True).execute()
        opciones, vistos = [], set()
        for item in res.data:
            num = item.get("numero_informe") or f"ID-{item['id']}"
            if num in vistos: continue
            vistos.add(num)
            opciones.append(f"{num} | {item.get('fecha', '')} | {item.get('tipo_mantenimiento', '')} | id:{item['id']}")
        return gr.update(choices=opciones, value=None)
    except Exception: return gr.update(choices=[], value=None)

def auto_completar_mantenimiento(serie_combo):
    try:
        if not serie_combo: return gr.update(choices=[], value=None)
        serie_ref = serie_combo[-1] if isinstance(serie_combo, list) and serie_combo else serie_combo
        serie_limpia = str(serie_ref).split(" - ")[0].strip()
        
        res = supabase.table("equipos").select("orden_id").ilike("numero_serie", f"{serie_limpia}%").execute()
        proveedor = ""
        if res.data and res.data[0].get("orden_id"):
            res_ord = supabase.table("ordenes_compra").select("razon_social_proveedor").eq("id", res.data[0]["orden_id"]).execute()
            if res_ord.data: proveedor = res_ord.data[0].get("razon_social_proveedor", "")
        return gr.update(choices=[proveedor] if proveedor else [], value=proveedor)
    except Exception: return gr.update(choices=[], value=None)

def obtener_numero_reporte(proveedor):
    if not proveedor or str(proveedor).strip() == "" or str(proveedor).lower() == "mantenimiento interno": prefix = "INT"
    else:
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', str(proveedor))
        prefix = cleaned[:3].upper() if len(cleaned) >= 3 else cleaned.upper().ljust(3, 'X')
    
    res = supabase.table("mantenimientos").select("numero_informe").like("numero_informe", f"MNT-{prefix}-%").execute()
    max_num = 0
    for item in res.data:
        num_str = item.get("numero_informe", "")
        if num_str:
            partes = num_str.split("-")
            if len(partes) >= 3:
                try:
                    num = int(partes[-1])
                    if num > max_num: max_num = num
                except ValueError: pass
    return f"MNT-{prefix}-{str(max_num + 1).zfill(4)}"

def registrar_y_generar_acta(funcionario_combo, serie_combo, fecha, tipo, checks, desc_extra, tecnico, costo, proximo, fotos_paths, foto_camara, nombre_admin, nombre_tecnico, nombre_func, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not funcionario_combo or not serie_combo or not fecha or not tipo: return "⚠️ Faltan datos.", [["-", "-", "-", "-", "-", "-", "-"]], gr.update(visible=False, value=None)

    # Lógica combinada para manejar fotos de archivo normal y fotos de la cámara web/celular
    if foto_camara:
        if fotos_paths is None:
            fotos_paths = []
        elif not isinstance(fotos_paths, list):
            fotos_paths = [fotos_paths]
        fotos_paths.append(foto_camara)

    nombre_custodio = str(funcionario_combo).split(" - ")[0].strip() if funcionario_combo else "Sin Asignar"
    trabajos = ", ".join(checks) if checks else ""
    desc_final = f"{trabajos}. {desc_extra}".strip(" .")

    mensajes, equipos_nombres, ordenes_encontradas = [], [], set()
    numero_reporte = obtener_numero_reporte(tecnico)

    for s in serie_combo:
        serie_limpia = str(s).split(" - ")[0].strip()
        equipos_nombres.append(s)
        try:
            res_eq = supabase.table("equipos").select("orden_id, ordenes_compra(numero_orden_compra, numero_proceso_sercop)").ilike("numero_serie", f"{serie_limpia}%").execute()
            if res_eq.data:
                ord_data = res_eq.data[0].get("ordenes_compra")
                if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
                elif not isinstance(ord_data, dict): ord_data = {}
                if ord_data:
                    num_ord = ord_data.get("numero_orden_compra") or ord_data.get("numero_proceso_sercop")
                    if num_ord: ordenes_encontradas.add(str(num_ord).strip())
        except Exception: pass

        try:
            supabase.table("mantenimientos").insert({
                "numero_serie": serie_limpia, "fecha": fecha, "tipo_mantenimiento": tipo, "descripcion": desc_final,
                "tecnico_proveedor": tecnico, "costo": float(costo) if costo else 0.0, "proximo_mantenimiento": proximo if proximo else None, "numero_informe": numero_reporte
            }).execute()
            if tipo == "De Baja": supabase.table("equipos").update({"estado": "De Baja"}).ilike("numero_serie", f"{serie_limpia}%").execute()
            mensajes.append(f"✅ Registrado: {serie_limpia}")
        except Exception as e: mensajes.append(f"❌ Error en {serie_limpia}: {e}")

    registrar_auditoria(usuario, f"Registró mantenimiento {numero_reporte}.")
    
    ruta_pdf = None
    if tipo in ["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"]:
        try:
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=20)
            pdf.add_page()
            
            def l(t): return str(t).replace('●','-').encode('latin-1', 'replace').decode('latin-1')
            
            pdf.set_fill_color(44, 62, 80); pdf.rect(0, 0, 210, 28, 'F'); pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", 'B', 16); pdf.set_xy(10, 8); pdf.cell(0, 10, "REPORTE DE MANTENIMIENTO", ln=True, align='C')
            pdf.set_font("Arial", '', 9); pdf.set_xy(10, 19); pdf.cell(0, 6, f"N. Reporte: {numero_reporte}", ln=True, align='C')
            pdf.set_text_color(0, 0, 0); pdf.ln(8)

            pdf.set_font("Arial", 'B', 11); pdf.set_fill_color(220, 230, 241); pdf.cell(0, 8, "DATOS GENERALES", ln=True, fill=True); pdf.ln(2)
            campos = [("Fecha de intervencion:", l(fecha)), ("Tipo de Mantenimiento:", l(tipo)), ("Empresa / Tecnico:", l(tecnico or "Interno")), ("Custodio a cargo:", l(nombre_custodio))]
            for et, val in campos: pdf.set_font("Arial", 'B', 10); pdf.cell(65, 7, et, 0, 0); pdf.set_font("Arial", '', 10); pdf.cell(0, 7, val, 0, 1)

            pdf.ln(4); pdf.set_font("Arial", 'B', 11); pdf.set_fill_color(220, 230, 241); pdf.cell(0, 8, "EQUIPOS INTERVENIDOS", ln=True, fill=True); pdf.ln(2)
            pdf.set_font("Arial", '', 10); 
            for eq in equipos_nombres: pdf.cell(0, 6, l(f"- {eq}"), ln=True)

            pdf.ln(4); pdf.set_font("Arial", 'B', 11); pdf.set_fill_color(220, 230, 241); pdf.cell(0, 8, "TRABAJOS Y CONCLUSIONES", ln=True, fill=True); pdf.ln(2)
            pdf.set_font("Arial", '', 10); pdf.multi_cell(0, 6, l(desc_final))

            pdf.ln(20); pdf.set_font("Arial", 'B', 11); pdf.set_fill_color(220, 230, 241); pdf.cell(0, 8, "FIRMAS DE CONFORMIDAD", ln=True, fill=True); pdf.ln(12)
            y = pdf.get_y(); anchos = 55; pos = [12, 78, 144]
            noms = [l(nombre_admin or "Administrador"), l(nombre_func or "Funcionario"), l(nombre_tecnico or "Tecnico")]
            roles = ["Administrador", "Funcionario", "Tecnico"]
            for i, x in enumerate(pos):
                pdf.line(x, y, x + anchos, y); pdf.set_xy(x, y + 2); pdf.set_font("Arial", 'B', 9); pdf.cell(anchos, 5, noms[i], 0, 2, 'C')
                pdf.set_font("Arial", 'I', 8); pdf.set_x(x); pdf.cell(anchos, 4, roles[i], 0, 2, 'C')

            ruta_pdf = os.path.join(tempfile.gettempdir(), f"Reporte_{numero_reporte}.pdf")
            pdf.output(ruta_pdf)

            try:
                ruta_storage = f"{numero_reporte}/Reporte_{numero_reporte}.pdf"
                with open(ruta_pdf, "rb") as f: supabase.storage.from_("reportes-mantenimiento").upload(ruta_storage, f, file_options={"content-type": "application/pdf", "upsert": "true"})
                supabase.table("mantenimientos").update({"url_reporte_pdf": ruta_storage}).eq("numero_informe", numero_reporte).execute()
            except Exception: pass
        except Exception: pass

    resumen = "\n".join(mensajes)
    hist = cargar_historial_por_funcionario(funcionario_combo) if funcionario_combo else [["-", "-", "-", "-", "-", "-", "-"]]
    if ruta_pdf: return f"✅ Reporte {numero_reporte} generado.\n{resumen}", gr.update(value=hist), gr.update(value=ruta_pdf, visible=True)
    return f"✅ Operación finalizada.\n{resumen}", gr.update(value=hist), gr.update(visible=False, value=None)

def descargar_reporte_mantenimiento(seleccion):
    if not seleccion: return gr.update(visible=False, value=None), "⚠️ Selecciona un registro."
    try:
        numero_informe = seleccion.split(" | ")[0].strip()
        res = supabase.table("mantenimientos").select("url_reporte_pdf").eq("numero_informe", numero_informe).limit(1).execute()
        if not res.data or not res.data[0].get("url_reporte_pdf"): return gr.update(visible=False, value=None), f"⚠️ Sin PDF."
        contenido = supabase.storage.from_("reportes-mantenimiento").download(res.data[0]["url_reporte_pdf"])
        ruta = os.path.join(tempfile.gettempdir(), f"Reporte_{numero_informe}.pdf")
        with open(ruta, "wb") as f: f.write(contenido)
        return gr.update(value=ruta, visible=True), f"✅ Descargado."
    except Exception as e: return gr.update(visible=False, value=None), f"❌ Error: {e}"

def eliminar_mantenimiento(seleccion, request: gr.Request):
    if not seleccion: return "⚠️ Selecciona un registro."
    try:
        num = seleccion.split(" | ")[0].strip()
        id_ref = int(seleccion.split("id:")[-1])
        res = supabase.table("mantenimientos").select("id, url_reporte_pdf").eq("numero_informe", num).execute() if num and not num.startswith("ID-") else supabase.table("mantenimientos").select("id, url_reporte_pdf").eq("id", id_ref).execute()
        
        ids_borrar = [r["id"] for r in res.data] if res.data else [id_ref]
        ruta_pdf = next((r.get("url_reporte_pdf") for r in res.data if r.get("url_reporte_pdf")), None) if res.data else None

        for id_reg in ids_borrar: supabase.table("mantenimientos").delete().eq("id", id_reg).execute()
        if ruta_pdf:
            try: supabase.storage.from_("reportes-mantenimiento").remove([ruta_pdf])
            except Exception: pass
            
        registrar_auditoria(request.username if request else "Sistema", f"Eliminó mantenimiento {num}.")
        return f"🗑️ Eliminado correctamente."
    except Exception as e: return f"❌ Error: {e}"

def cargar_datos_mantenimiento_edicion(seleccion):
    if not seleccion: return "", "", "", "", "", 0.0
    try:
        numero_informe = seleccion.split(" | ")[0].strip()
        id_referencia = int(seleccion.split("id:")[-1])
        if numero_informe and not numero_informe.startswith("ID-"):
            res = supabase.table("mantenimientos").select("*").eq("numero_informe", numero_informe).limit(1).execute()
        else:
            res = supabase.table("mantenimientos").select("*").eq("id", id_referencia).execute()

        if res.data:
            item = res.data[0]
            return (
                item.get("fecha", ""),
                item.get("proximo_mantenimiento", ""),
                item.get("tipo_mantenimiento", ""),
                item.get("tecnico_proveedor", ""),
                item.get("descripcion", ""),
                float(item.get("costo", 0.0))
            )
        return "", "", "", "", "", 0.0
    except Exception as e:
        print(f"Error cargando edicion mant: {e}")
        return "", "", "", "", "", 0.0

def guardar_edicion_mantenimiento(seleccion, fecha, tipo, tecnico, desc, costo, proximo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not seleccion: return "⚠️ Selecciona un registro primero en el desplegable de arriba."
    try:
        numero_informe = seleccion.split(" | ")[0].strip()
        id_referencia = int(seleccion.split("id:")[-1])

        datos_actualizar = {
            "fecha": fecha,
            "tipo_mantenimiento": tipo,
            "tecnico_proveedor": tecnico,
            "descripcion": desc,
            "costo": float(costo) if costo else 0.0,
            "proximo_mantenimiento": proximo if proximo else None
        }

        if numero_informe and not numero_informe.startswith("ID-"):
            supabase.table("mantenimientos").update(datos_actualizar).eq("numero_informe", numero_informe).execute()
        else:
            supabase.table("mantenimientos").update(datos_actualizar).eq("id", id_referencia).execute()

        registrar_auditoria(usuario, f"Editó registro de mantenimiento: {numero_informe}")
        return f"✅ Registro {numero_informe} actualizado correctamente."
    except Exception as e: return f"❌ Error al editar: {e}"

def analizar_acta_pdf(archivo_acta):
    if not archivo_acta: return None, {}, "⚠️ Sube un Acta."
    try:
        t = "".join(p.extract_text() + "\n" for p in PdfReader(archivo_acta).pages)
        p = f"""Extrae a JSON puro (sin markdown ni comillas raras):
{{
    "numero_proceso_sercop": "Proceso", "numero_orden_compra": "Orden",
    "razon_social_proveedor": "Proveedor", "nombre_comercial": "Nombre Comercial", "objeto_contratacion": "Objeto", "monto": 0.0,
    "equipos": [ {{"tipo": "Laptop", "marca": "Dell", "modelo": "Latitude", "serie": "123", "observaciones": ""}} ]
}}
Acta: {t}"""
        res = llamar_gemini_con_reintentos(p).text.strip()
        for m in ["```json", "```"]: res = res.replace(m, "")
        d = json.loads(res.strip())
        
        f = []
        for eq in d.get("equipos", []):
            f.append([eq.get("tipo",""), eq.get("marca",""), eq.get("modelo",""), eq.get("serie",""), d.get("razon_social_proveedor",""), d.get("nombre_comercial",""), d.get("numero_orden_compra",""), d.get("numero_proceso_sercop",""), eq.get("observaciones","")])
        return f, d, "✅ Acta leída. Edita en la tabla y Guarda."
    except Exception as e: return None, {}, f"❌ Error: {e}"

def procesar_acta_recepcion(tabla_datos, state_datos, request: gr.Request):
    if tabla_datos is None or tabla_datos.empty: return "⚠️ Tabla vacía.", cargar_datos_inventario(), obtener_series_disponibles()
    try:
        prov, nomc, ord_num, proc = str(tabla_datos.iloc[0,4]).strip(), str(tabla_datos.iloc[0,5]).strip(), str(tabla_datos.iloc[0,6]).strip(), str(tabla_datos.iloc[0,7]).strip()
        
        resp_ord = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proc).eq("numero_orden_compra", ord_num).execute()
        if not resp_ord.data: resp_ord = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{proc} [Ref: {ord_num}]").eq("numero_orden_compra", ord_num).execute()
        
        if resp_ord.data:
            o_id = resp_ord.data[0]['id']
            supabase.table("ordenes_compra").update({"razon_social_proveedor": prov, "nombre_comercial": nomc}).eq("id", o_id).execute()
        else:
            proc_g = f"{proc} [Ref: {ord_num}]" if supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proc).execute().data else proc
            if not proc_g or proc_g in ["Sin Proceso", "-", "nan", ""]: proc_g = f"MANUAL-{int(time.time())}"
            o_id = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": proc_g, "numero_orden_compra": ord_num, "razon_social_proveedor": prov, "nombre_comercial": nomc, "objeto_contratacion": state_datos.get("objeto_contratacion", ""), "monto": float(state_datos.get("monto", 0)), "fecha_adquisicion": "2023-01-01"}).execute().data[0]['id']
            
        m_db = {str(eq["numero_serie"]).strip().upper().replace(" ", ""): eq["id"] for eq in supabase.table("equipos").select("id, numero_serie").execute().data if eq.get("numero_serie")}
        n, a = 0, 0

        for _, r in tabla_datos.iterrows():
            t, ma, mo, s, ob = str(r.iloc[0]).strip(), str(r.iloc[1]).strip(), str(r.iloc[2]).strip(), str(r.iloc[3]).strip(), str(r.iloc[8]).strip()
            if not s or s.lower() == 'nan': continue
            e_id = m_db.get(s.upper().replace(" ", ""))
            if e_id: supabase.table("equipos").update({"tipo_equipo": t, "marca": ma, "modelo": mo, "observaciones": ob}).eq("id", e_id).execute(); a+=1
            else: supabase.table("equipos").insert({"orden_id": o_id, "tipo_equipo": t, "marca": ma, "modelo": mo, "numero_serie": s, "estado": "Operativo", "observaciones": ob}).execute(); n+=1
                
        registrar_auditoria(request.username if request else "Sistema", f"Acta procesada: {n} nuevos, {a} act.")
        return f"✅ {n} NUEVOS | 🔄 {a} ACTUALIZADOS.", cargar_datos_inventario(), obtener_series_disponibles()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles()

def ingresar_equipo_manual(proc, ord_num, prov, nom_com, obj, monto, tipo, marca, modelo, serie, obs, request: gr.Request):
    if not ord_num or not serie: return "⚠️ Faltan datos.", cargar_datos_inventario(), obtener_series_disponibles()
    try:
        proc_l = str(proc).strip()
        resp_ord = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proc_l).eq("numero_orden_compra", str(ord_num).strip()).execute()
        if not resp_ord.data: resp_ord = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{proc_l} [Ref: {str(ord_num).strip()}]").eq("numero_orden_compra", str(ord_num).strip()).execute()
        
        if resp_ord.data: 
            o_id = resp_ord.data[0]['id']
            upd = {}
            if prov: upd["razon_social_proveedor"] = prov
            if nom_com: upd["nombre_comercial"] = nom_com
            if upd: supabase.table("ordenes_compra").update(upd).eq("id", o_id).execute()
        else:
            proc_g = f"{proc_l} [Ref: {ord_num}]" if supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proc_l).execute().data else proc_l
            if not proc_g or proc_g in ["Sin Proceso", "-", "nan", ""]: proc_g = f"MANUAL-{int(time.time())}"
            o_id = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": proc_g, "numero_orden_compra": ord_num, "razon_social_proveedor": prov or "", "nombre_comercial": nom_com or "", "objeto_contratacion": obj or "", "monto": float(monto or 0), "fecha_adquisicion": "2023-01-01"}).execute().data[0]['id']
            
        supabase.table("equipos").insert({"orden_id": o_id, "tipo_equipo": tipo or "", "marca": marca or "", "modelo": modelo or "", "numero_serie": serie, "estado": "Operativo", "observaciones": obs or ""}).execute()
        registrar_auditoria(request.username if request else "Sistema", f"Equipo manual {serie}.")
        return f"✅ Equipo '{serie}' ingresado.", cargar_datos_inventario(), obtener_series_disponibles()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles()

def analizar_excel_masivo(archivo_excel):
    if not archivo_excel: return "⚠️ Sube Excel.", []
    try:
        df_raw = pd.read_excel(archivo_excel, header=None)
        h_idx = next((i for i, r in df_raw.iterrows() if 'serie' in " ".join(str(v).lower() for v in r.values) and ('nombre' in " ".join(str(v).lower() for v in r.values) or 'custodio' in " ".join(str(v).lower() for v in r.values))), -1)
        if h_idx == -1: return "❌ Sin tabla.", []
        df = pd.read_excel(archivo_excel, header=h_idx)
        df.columns = df.columns.astype(str).str.strip().str.lower()
        
        c_serie = next((c for c in df.columns if 'serie' in c), None)
        c_ced = next((c for c in df.columns if any(x in c for x in ['cedula','cédula','identificacion','cód','cod'])), None)
        c_nom = next((c for c in df.columns if any(x in c for x in ['nombre','funcionario','custodio'])), None)
        c_car = next((c for c in df.columns if 'cargo' in c), None)
        c_dep = next((c for c in df.columns if any(x in c for x in ['departamento','area','dirección'])), None)

        eq_db = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in supabase.table("equipos").select("numero_serie").execute().data}
        f_db = {str(f.get("nombres_completos", "")).strip().upper(): str(f.get("cedula", "")).strip() for f in supabase.table("funcionarios").select("cedula, nombres_completos").execute().data}
        
        vp = []
        for _, r in df.iterrows():
            nom = str(r[c_nom]).strip() if c_nom and pd.notna(r[c_nom]) else "Sin Nombre"
            ser = str(r[c_serie]).strip().upper().replace(" ", "") if c_serie and pd.notna(r[c_serie]) else ""
            if ser.endswith('.0'): ser = ser[:-2]
            ced = str(r[c_ced]).strip() if c_ced and pd.notna(r[c_ced]) else ""
            if ced.endswith('.0'): ced = ced[:-2]
            if not ced or ced.lower() == 'nan':
                for v in r.values:
                    vs = str(v).strip().replace(".0", "")
                    if vs.isdigit() and len(vs) in [9, 10]: ced = vs; break
            if len(ced) == 9 and ced.isdigit(): ced = "0" + ced
            if (not ced or ced.lower() == 'nan') and nom != "Sin Nombre": ced = f_db.get(nom.upper(), "")

            car = str(r[c_car]).strip() if c_car and pd.notna(r[c_car]) else "No definido"
            dep = str(r[c_dep]).strip() if c_dep and pd.notna(r[c_dep]) else "No definido"

            if not ser and not ced and nom == "Sin Nombre": continue
            sm = ser if ser and ser != 'NAN' else ""
            cm = ced if ced and ced.lower() != 'nan' else ""
            if not sm: vp.append(["", cm, nom, car, dep, "❌ ERROR: Falta serie"])
            elif not eq_db.get(sm): vp.append([sm, cm, nom, car, dep, "❌ ERROR: Equipo no en BD"])
            elif not cm: vp.append([eq_db.get(sm), "", nom, car, dep, "❌ ERROR: Falta ID"])
            else: vp.append([eq_db.get(sm), cm, nom, car, dep, "✅ OK"])

        return f"✅ Listo. {sum(1 for f in vp if '✅' in f[-1])} válidos.", vp
    except Exception as e: return f"❌ Error: {e}", []

def confirmar_asignacion_masiva(tabla_datos, request: gr.Request):
    if tabla_datos is None or tabla_datos.empty: return "⚠️ Tabla vacía."
    m_f = {str(f["cedula"]).strip(): f["id"] for f in supabase.table("funcionarios").select("cedula, id").execute().data}
    eq_v = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in supabase.table("equipos").select("numero_serie").execute().data}

    ex, nu, er = 0, 0, 0
    for _, r in tabla_datos.iterrows():
        try:
            s, c, n, car, d = str(r.iloc[0]).strip(), str(r.iloc[1]).strip(), str(r.iloc[2]).strip(), str(r.iloc[3]).strip(), str(r.iloc[4]).strip()
            if not s or not c or s.lower() == 'nan': er+=1; continue
            se = eq_v.get(s.upper().replace(" ", ""))
            if not se: er+=1; continue
            
            fid = m_f.get(c)
            if not fid:
                res = supabase.table("funcionarios").insert({"cedula": c, "nombres_completos": n, "cargo": car, "departamento": d}).execute()
                if res.data: fid = res.data[0]["id"]; m_f[c] = fid; nu += 1
            if fid: supabase.table("equipos").update({"funcionario_id": fid}).eq("numero_serie", se).execute(); ex += 1
        except Exception: er += 1
    registrar_auditoria(request.username if request else "Sistema", f"Carga masiva: {ex} asign.")
    return f"✅ Completado: {ex} asignados, {nu} nuevos. (Errores: {er})"

def cargar_tabla_edicion(termino=""):
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        d = []
        for eq in res.data:
            ord_c = eq.get("ordenes_compra")
            if isinstance(ord_c, list) and ord_c: ord_c = ord_c[0]
            prov = ord_c.get("razon_social_proveedor", "") if ord_c else ""
            nomc = ord_c.get("nombre_comercial", "") if ord_c else ""

            func = eq.get("funcionarios")
            cust = func.get("nombres_completos") if func else "Sin Asignar"
            dept = func.get("departamento") if func else "-"
            obs = eq.get("observaciones", "") or ""

            fs = f"{eq.get('numero_serie','')} {eq.get('tipo_equipo','')} {eq.get('marca','')} {eq.get('modelo','')} {prov} {nomc} {cust} {dept}".lower()
            if termino and termino.lower() not in fs: continue

            d.append([eq["id"], eq.get("numero_serie",""), eq.get("tipo_equipo",""), eq.get("marca",""), eq.get("modelo",""), cust, dept, eq.get("estado",""), prov, nomc, obs, "Mantener"])
        return d if d else [["-", "Sin datos", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def cargar_duplicados():
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        cont = {}
        for eq in res.data: s = str(eq.get("numero_serie","")).upper().replace(" ", ""); cont[s] = cont.get(s, 0) + 1

        d = []
        for eq in res.data:
            if cont[str(eq.get("numero_serie","")).upper().replace(" ", "")] > 1:
                ord_c = eq.get("ordenes_compra")
                if isinstance(ord_c, list) and ord_c: ord_c = ord_c[0]
                prov = ord_c.get("razon_social_proveedor", "") if ord_c else ""
                nomc = ord_c.get("nombre_comercial", "") if ord_c else ""

                func = eq.get("funcionarios")
                cust = func.get("nombres_completos") if func else "Sin Asignar"
                dept = func.get("departamento") if func else "-"
                
                d.append([eq["id"], eq.get("numero_serie",""), eq.get("tipo_equipo",""), eq.get("marca",""), eq.get("modelo",""), cust, dept, eq.get("estado",""), prov, nomc, eq.get("observaciones","") or "", "Mantener"])
        d.sort(key=lambda x: str(x[1]).upper().replace(" ", ""))
        return d if d else [["-", "Sin duplicados", "-", "-", "-", "-", "-", "-", "-", "-", "-", "Mantener"]]
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def confirmar_edicion_masiva(df, request: gr.Request):
    if df is None or df.empty: return "⚠️ Tabla vacía."
    e_ed, e_del, err = 0, 0, 0

    for _, r in df.iterrows():
        try:
            idb_r = str(r.iloc[0]).strip().replace(",", "")
            if idb_r.endswith('.0'): idb_r = idb_r[:-2]
            if idb_r in ["-", "nan", ""]: continue
            idb = idb_r
            
            acc = str(r.iloc[11]).strip()
            if acc.lower() == "eliminar":
                supabase.table("equipos").delete().eq("id", idb).execute(); e_del += 1
            else:
                ser = str(r.iloc[1]).strip()
                if ser.endswith('.0'): ser = ser[:-2]
                
                d_act = {"numero_serie": ser, "tipo_equipo": str(r.iloc[2]).strip(), "marca": str(r.iloc[3]).strip(), "modelo": str(r.iloc[4]).strip(), "estado": str(r.iloc[7]).strip(), "observaciones": str(r.iloc[10]).strip()}

                res_eq = supabase.table("equipos").select("orden_id, funcionario_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").eq("id", idb).execute()
                if res_eq.data:
                    fid = res_eq.data[0].get("funcionario_id")
                    if fid: supabase.table("funcionarios").update({"nombres_completos": str(r.iloc[5]).strip(), "departamento": str(r.iloc[6]).strip()}).eq("id", fid).execute()

                    pn, nn = str(r.iloc[8]).strip(), str(r.iloc[9]).strip()
                    ord_d = res_eq.data[0].get("ordenes_compra")
                    if isinstance(ord_d, list) and ord_d: ord_d = ord_d[0]
                    elif not isinstance(ord_d, dict): ord_d = {}
                    
                    if pn and pn != ord_d.get("razon_social_proveedor", "").strip():
                        r_ind = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"INDIV-{ser}").execute()
                        if r_ind.data: 
                            supabase.table("ordenes_compra").update({"razon_social_proveedor": pn, "nombre_comercial": nn}).eq("id", r_ind.data[0]["id"]).execute()
                            d_act["orden_id"] = r_ind.data[0]["id"]
                        else: 
                            d_act["orden_id"] = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": f"INDIV-{ser}", "numero_orden_compra": "Independiente", "razon_social_proveedor": pn, "nombre_comercial": nn, "objeto_contratacion": "Separación", "monto": 0.0, "fecha_adquisicion": "2023-01-01"}).execute().data[0]["id"]
                    else:
                        if nn != ord_d.get("nombre_comercial", "").strip():
                            supabase.table("ordenes_compra").update({"nombre_comercial": nn}).eq("id", res_eq.data[0].get("orden_id")).execute()

                supabase.table("equipos").update(d_act).eq("id", idb).execute(); e_ed += 1
        except Exception: err += 1

    registrar_auditoria(request.username if request else "Sistema", f"Edición Masiva: {e_ed} act, {e_del} elim.")
    return f"✅ {e_ed} act., {e_del} elim. (Errores: {err})"

def cargar_equipo_para_edicion(serie_combo):
    if not serie_combo: return "", "", "", "", "", "", "", "", None
    try:
        res = supabase.table("equipos").select("numero_serie, marca, modelo, estado, tipo_equipo, observaciones, orden_id, funcionario_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, cedula)").ilike("numero_serie", f"{serie_combo.split(' - ')[0].strip()}%").execute()
        if res.data:
            it = res.data[0]
            ord_d = it.get('ordenes_compra')
            if isinstance(ord_d, list) and ord_d: ord_d = ord_d[0]
            func = it.get('funcionarios')
            if isinstance(func, list) and func: func = func[0]
            elif not isinstance(func, dict): func = {}

            cust = f"{func.get('nombres_completos','')} - {func.get('cedula','')}" if it.get('funcionario_id') and func else "Sin Asignar - BODEGA"
            return it.get('numero_serie','') or '', it.get('marca','') or '', it.get('modelo','') or '', it.get('estado',''), it.get('tipo_equipo',''), ord_d.get('razon_social_proveedor','') if ord_d else '', ord_d.get('nombre_comercial','') if ord_d else '', it.get('observaciones','') or '', cust
        return "", "", "", "", "", "", "", "", None
    except Exception: return "", "", "", "", "", "", "", "", None

def guardar_edicion_equipo(s_combo, s_n, m_n, mod_n, est_n, tip_n, p_n, nc_n, obs_n, c_n, request: gr.Request):
    if not s_combo: return "⚠️ Selecciona equipo."
    s_l = s_combo.split(" - ")[0].strip()
    s_nl = (s_n or "").strip()
    if not s_nl: return "⚠️ Serie no puede quedar vacía."

    try:
        res_eq = supabase.table("equipos").select("id, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").ilike("numero_serie", f"{s_l}%").execute()
        if not res_eq.data: return "❌ Equipo no encontrado."

        e_id = res_eq.data[0]['id']
        ord_d = res_eq.data[0].get("ordenes_compra")
        if isinstance(ord_d, list) and ord_d: ord_d = ord_d[0]
        elif not isinstance(ord_d, dict): ord_d = {}

        if s_nl != s_l:
            if supabase.table("equipos").select("id").eq("numero_serie", s_nl).execute().data: return f"❌ Ya existe serie '{s_nl}'."

        d_act = {"numero_serie": s_nl, "estado": est_n, "tipo_equipo": tip_n, "marca": m_n, "modelo": mod_n, "observaciones": obs_n}
        m_ext = ""

        if c_n:
            if str(c_n).strip() == "Sin Asignar - BODEGA": d_act["funcionario_id"] = None
            else:
                r_f = supabase.table("funcionarios").select("id").eq("cedula", str(c_n).strip().split(" - ")[-1].strip()).execute()
                if r_f.data: d_act["funcionario_id"] = r_f.data[0]["id"]

        p_nl, nc_nl = (p_n or "").strip(), (nc_n or "").strip()
        p_act, nc_act = (ord_d.get("razon_social_proveedor", "") or "").strip(), (ord_d.get("nombre_comercial", "") or "").strip()

        if (p_nl and p_nl != p_act) or (nc_nl and nc_nl != nc_act):
            id_u = f"INDIV-{s_nl}"
            r_ind = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", id_u).execute()
            if r_ind.data:
                o_nid = r_ind.data[0]["id"]
                supabase.table("ordenes_compra").update({"razon_social_proveedor": p_nl, "nombre_comercial": nc_nl}).eq("id", o_nid).execute()
            else:
                o_nid = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": id_u, "numero_orden_compra": "Independiente", "razon_social_proveedor": p_nl, "nombre_comercial": nc_nl, "objeto_contratacion": "Separación", "monto": 0.0, "fecha_adquisicion": "2023-01-01"}).execute().data[0]["id"]
            d_act["orden_id"] = o_nid
            m_ext += " (Prov. independizado)."

        supabase.table("equipos").update(d_act).eq("id", e_id).execute()
        if s_nl != s_l: supabase.table("mantenimientos").update({"numero_serie": s_nl}).ilike("numero_serie", f"{s_l}%").execute()

        registrar_auditoria(request.username if request else "Sistema", f"Editó equipo {s_l}.")
        return f"✅ Guardado.{m_ext}"
    except Exception as e: return f"❌ Error: {e}"

def generar_reporte_personalizado(o, c, col, formato):
    d = cargar_datos_inventario("")
    h_c = ["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"]
    if not col: return gr.update(visible=False, value=None), "⚠️ Selecciona columnas."
    d_f = []
    for f in d:
        if f[0] == "-": continue
        if o and o.lower() not in (str(f[6]) + str(f[7])).lower(): continue
        if c and c.lower() not in (str(f[3]) + str(f[4])).lower(): continue
        d_f.append([f[h_c.index(x)] for x in col])
    if not d_f: return gr.update(visible=False, value=None), "⚠️ Sin resultados."
    
    td = tempfile.gettempdir()
    fs = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if formato == "excel":
            ruta = os.path.join(td, f"Reporte_{fs}.xlsx")
            pd.DataFrame(d_f, columns=col).to_excel(ruta, index=False)
            return gr.update(value=ruta, visible=True), "✅ Excel generado."
        elif formato == "pdf":
            ruta = os.path.join(td, f"Reporte_{fs}.pdf")
            pdf = FPDF(orientation='L', unit='mm', format='A4')
            pdf.add_page(); pdf.set_font("Arial", 'B', 14); pdf.cell(0, 12, "REPORTE INVENTARIO", ln=True, align='C')
            def l(t):
                ts = str(t)
                for og, rem in {'●':'-','✓':'OK','✗':'X'}.items(): ts = ts.replace(og, rem)
                return ts.encode('latin-1', 'replace').decode('latin-1')
            ps = {"Equipo": 1.5, "Marca/Modelo": 2.0, "Nº Serie": 1.5, "Custodio Asignado": 2.5, "Área/Dirección": 2.0, "Estado": 1.0, "Orden de Compra": 1.5, "Proceso SERCOP": 1.5, "Proveedor": 2.0, "Nombre Comercial": 2.0, "Observaciones": 3.0}
            pt = sum(ps[x] for x in col); anchos = [(ps[x]/pt)*277 for x in col]
            pdf.set_font("Arial", 'B', 8)
            for i, x in enumerate(col): pdf.cell(anchos[i], 8, l(x), 1, 0, 'C')
            pdf.ln()
            pdf.set_font("Arial", '', 7)
            for f in d_f:
                for i, it in enumerate(f):
                    txt = l(it)
                    mc = int(anchos[i]/1.5)
                    pdf.cell(anchos[i], 6, txt[:mc-2]+".." if len(txt)>mc else txt, 1)
                pdf.ln()
            pdf.output(ruta)
            return gr.update(value=ruta, visible=True), "✅ PDF generado."
    except Exception as e: return gr.update(visible=False, value=None), f"❌ Error: {e}"

def exportar_pdf_inv(o, c, col): return generar_reporte_personalizado(o, c, col, "pdf")
def exportar_excel_inv(o, c, col): return generar_reporte_personalizado(o, c, col, "excel")

def cargar_todo_ui():
    return (cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_listado_funcionarios(), obtener_funcionarios(), obtener_usuarios_lista(), obtener_funcionarios_mantenimiento())

def inicializar_sistema_completo(request: gr.Request):
    ea = False
    try:
        r = supabase.table("usuarios_sistema").select("rol").eq("usuario", request.username if request else "").execute()
        if r.data and r.data[0].get('rol') == 'admin': ea = True
    except: pass
    return (cargar_datos_auditoria(), cargar_usuarios_sistema(), cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_listado_funcionarios(), obtener_funcionarios(), obtener_usuarios_lista(), obtener_funcionarios_mantenimiento(), gr.update(visible=ea), gr.update(visible=not ea))

def seleccionar_funcionario_tabla(evt: gr.SelectData, tab):
    try:
        c, n = str(tab.iloc[evt.index[0], 0]), str(tab.iloc[evt.index[0], 1])
        return c, n, str(tab.iloc[evt.index[0], 2]), str(tab.iloc[evt.index[0], 3]), f"{n} - {c}"
    except: return "", "", "", "", None

def seleccionar_usuario_tabla(evt: gr.SelectData, tab):
    try:
        u, r = str(tab.iloc[evt.index[0], 0]), str(tab.iloc[evt.index[0], 1]).lower()
        return u, r if r in ["admin", "operador"] else "admin", f"{u} - {r}"
    except: return "", "admin", None

def seleccionar_inventario_tabla(evt: gr.SelectData, tab):
    try:
        if tab is None or len(tab) == 0: return None, "", "", None
        f = evt.index[0]
        if isinstance(tab, pd.DataFrame): t, s, c, e = str(tab.iloc[f, 0]), str(tab.iloc[f, 2]), str(tab.iloc[f, 3]), str(tab.iloc[f, 5])
        else: t, s, c, e = str(tab[f][0]), str(tab[f][2]), str(tab[f][3]), str(tab[f][5])
        return f"{s} - {t}", e, t, (f"{s} - {t} ({c})" if c != "Sin Asignar" else None)
    except: return None, "", "", None

def cargar_datos_funcionario_form(combo):
    if not combo: return "", "", "", ""
    r = supabase.table("funcionarios").select("*").eq("cedula", str(combo).split(" - ")[-1].strip()).execute()
    if r.data: f = r.data[0]; return f.get("cedula",""), f.get("nombres_completos",""), f.get("cargo",""), f.get("departamento","")
    return "", "", "", ""

def cargar_datos_usuario_form(combo):
    if not combo: return "", "admin"
    res = supabase.table("usuarios_sistema").select("*").eq("usuario", str(combo).split(" - ")[0].strip()).execute()
    if res.data: return res.data[0].get("usuario",""), res.data[0].get("rol","admin")
    return "", "admin"

def advertencia_asignacion(c):
    if not c: return ""
    try:
        rf = supabase.table("funcionarios").select("id").eq("cedula", c.split(" - ")[-1]).execute()
        if rf.data:
            re = supabase.table("equipos").select("tipo_equipo").eq("funcionario_id", rf.data[0]['id']).execute()
            if len(re.data) > 0: return f"⚠️ ADVERTENCIA: Esta persona ya tiene {len(re.data)} equipo(s)."
        return "ℹ️ Sin equipos asignados."
    except: return ""

def eliminar_funcionario(c, request: gr.Request):
    cl = c.strip() if c else ""
    if not cl: return "⚠️ Debes ingresar la cédula.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        rf = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cl).execute()
        if not rf.data: return "⚠️ No encontrado.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
        fid, n = rf.data[0]['id'], rf.data[0]['nombres_completos']
        
        re = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").eq("funcionario_id", fid).execute()
        if re.data and len(re.data) > 0: return f"❌ ALERTA: No se puede eliminar a {n} porque tiene {len(re.data)} equipo(s). Libéralos primero.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

        supabase.table("funcionarios").delete().eq("cedula", cl).execute()
        registrar_auditoria(request.username if request else "Sistema", f"Eliminó funcionario: {n}")
        return f"🗑️ {n} eliminado.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def registrar_y_actualizar(c, n, car, d, request: gr.Request):
    cl = c.strip()
    if not cl or not n: return "⚠️ Faltan datos.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        r = supabase.table("funcionarios").select("id").eq("cedula", cl).execute()
        if r.data:
            supabase.table("funcionarios").update({"nombres_completos": n, "cargo": car, "departamento": d}).eq("cedula", cl).execute()
            msg = "✅ Actualizado."
        else:
            supabase.table("funcionarios").insert({"cedula": cl, "nombres_completos": n, "cargo": car, "departamento": d}).execute()
            msg = "✅ Creado."
        registrar_auditoria(request.username if request else "Sistema", f"Funcionario {n}.")
        return msg, obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def asignar_custodio_equipo(s_c, f_c, gar, request: gr.Request):
    if not s_c or not f_c: return "⚠️ Faltan selecciones.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    s_l, cl, nom = s_c.split(" - ")[0].strip(), f_c.split(" - ")[-1].strip(), f_c.split(" - ")[0].strip()
    try:
        rf = supabase.table("funcionarios").select("id").eq("cedula", cl).execute()
        if not rf.data: return "❌ Funcionario no encontrado.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
            
        supabase.table("equipos").update({"funcionario_id": rf.data[0]['id']}).eq("numero_serie", s_l).execute()
        m = f"✅ Equipo {s_l} asignado a {nom}."
        if gar: m += f"\n📜 GARANTÍA:\n{llamar_gemini_con_reintentos(f'Extrae garantia: {''.join(p.extract_text() for p in PdfReader(gar).pages)}').text.strip()}"
        registrar_auditoria(request.username if request else "Sistema", f"Asignó {s_l} a {nom}.")
        return m, cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()

def liberar_equipo(s_c, request: gr.Request):
    if not s_c: return "⚠️ Selecciona.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()
    s_l = s_c.split(" - ")[0].strip()
    try:
        supabase.table("equipos").update({"funcionario_id": None}).eq("numero_serie", s_l).execute()
        registrar_auditoria(request.username if request else "Sistema", f"Liberó {s_l}.")
        return f"🔓 {s_l} liberado.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()

def eliminar_equipo_inventario(s_c, m, request: gr.Request):
    if not s_c: return "⚠️ Selecciona.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
    if not m or not str(m).strip(): return "⚠️ Motivo obligatorio.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
    s_l = str(s_c).split(" - ")[0].strip()
    try:
        re = supabase.table("equipos").select("id, numero_serie, observaciones, funcionario_id").ilike("numero_serie", f"{s_l}%").execute()
        if not re.data: return f"❌ {s_l} no encontrado.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
        if re.data[0].get("funcionario_id") is not None: return f"❌ Libera primero a {re.data[0]['numero_serie']}.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
        
        n_o = f"[ELIMINADO: {str(m).strip()}] {(re.data[0].get('observaciones') or '')}".strip()
        supabase.table("equipos").update({"estado": "De Baja", "observaciones": n_o, "funcionario_id": None}).eq("id", re.data[0]["id"]).execute()
        registrar_auditoria(request.username if request else "Sistema", f"Dio de baja {re.data[0]['numero_serie']}.")
        return f"🗑️ {re.data[0]['numero_serie']} de baja.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update(value="")
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()


with gr.Blocks() as erp_interfaz:
    with gr.Row():
        gr.Markdown("<h1 style='text-align: left; width: 80%; color: #2c3e50; margin-left: 10px;'>🏛️ Sistema Integrado de Contratación y Vigencia Tecnológica</h1>")
        btn_logout = gr.Button("🚪 Cerrar Sesión", variant="stop", scale=1)
    
    with gr.Tabs():
        with gr.TabItem("1. Análisis Precontractual"):
            gr.Markdown("### 🧠 Auditoría Inteligente de Pliegos y TDRs")
            with gr.Row():
                with gr.Column(scale=1):
                    archivos_input = gr.File(label="Documentos a Auditar (PDF)", file_count="multiple")
                    referencias_input = gr.File(label="Normativas de Referencia (PDF)", file_count="multiple")
                    enlaces_input = gr.Textbox(label="Enlaces Adicionales", lines=2)
                    btn_analizar = gr.Button("Generar Informe", variant="primary")
                with gr.Column(scale=2):
                    reporte_output = gr.Textbox(label="📊 Informe de Auditoría Detallado", lines=20)

        with gr.TabItem("2. Custodios e Inventario"):
            with gr.Row():
                with gr.Column(scale=1):
                    with gr.Group():
                        gr.Markdown("#### 📥 Ingreso de Bienes")
                        with gr.Accordion("1. Ingreso Automático (Acta PDF)", open=False):
                            acta_input = gr.File(label="Subir Acta (PDF)", type="filepath")
                            btn_analizar_acta = gr.Button("1. Extraer y Previsualizar Datos", variant="secondary")
                            tabla_preview_acta = gr.Dataframe(headers=["Tipo", "Marca", "Modelo", "Serie", "Proveedor", "Nombre Comercial", "Orden de Compra", "Proceso SERCOP", "Observaciones"], interactive=True, wrap=True)
                            datos_acta_state = gr.State({})
                            btn_procesar_acta = gr.Button("2. Confirmar y Guardar en BD", variant="primary")
                            mensaje_acta = gr.Textbox(label="Estado", interactive=False)
                            
                        with gr.Accordion("2. Ingreso Manual de Equipos", open=False):
                            num_proceso_man = gr.Textbox(label="Proceso SERCOP")
                            num_orden_man = gr.Textbox(label="Orden de Compra (CE-...) *")
                            proveedor_man = gr.Textbox(label="Proveedor (Razón Social)")
                            nombre_comercial_man = gr.Textbox(label="Nombre Comercial (Proveedor)")
                            objeto_man = gr.Textbox(label="Objeto de Contratación")
                            monto_man = gr.Number(label="Monto Total ($)", value=0.0)
                            tipo_man = gr.Textbox(label="Tipo de Equipo *")
                            marca_man = gr.Textbox(label="Marca")
                            modelo_man = gr.Textbox(label="Modelo")
                            serie_man = gr.Textbox(label="Número de Serie *")
                            observaciones_man = gr.Textbox(label="Observaciones Relevantes del Equipo")
                            btn_guardar_manual = gr.Button("Guardar", variant="secondary")
                            mensaje_manual = gr.Textbox(label="Estado", interactive=False)

                    with gr.Group():
                        gr.Markdown("#### 👤 Gestión de Personal")
                        with gr.Accordion("3. Listado e Ingreso de Personal", open=False):
                            buscar_func_combo = gr.Dropdown(label="Buscar Funcionario Registrado", choices=[], interactive=True)
                            cedula_in = gr.Textbox(label="Código del GAD (Cédula)")
                            nombres_in = gr.Textbox(label="Nombres Completos")
                            cargo_in = gr.Textbox(label="Cargo")
                            depto_in = gr.Textbox(label="Departamento")
                            with gr.Row():
                                btn_guardar_func = gr.Button("💾 Guardar", variant="primary")
                                btn_limpiar_func = gr.Button("🧹 Limpiar", variant="secondary")
                                btn_eliminar_func = gr.Button("🗑️ Eliminar", variant="stop")
                            mensaje_func = gr.Textbox(show_label=False, interactive=False, lines=4)
                            tabla_funcionarios = gr.Dataframe(headers=["Código del GAD", "Nombre Completo", "Cargo", "Departamento"], interactive=False, wrap=True)
                    
                    with gr.Group():
                        gr.Markdown("#### 🔗 Asignación de Equipos")
                        with gr.Accordion("4. Asignación Individual", open=False):
                            serie_asignar = gr.Dropdown(label="Equipo Libre", choices=[], interactive=True)
                            custodio_asignar = gr.Dropdown(label="Custodio", choices=[], interactive=True)
                            alerta_asignacion = gr.Markdown("")
                            garantia_input = gr.File(label="Garantía (Opcional)", type="filepath")
                            btn_asignar = gr.Button("Vincular", variant="primary")
                            mensaje_asignar = gr.Textbox(label="Estado", lines=2, interactive=False)
                        
                        with gr.Accordion("5. Asignación Masiva (Excel)", open=False):
                            excel_input = gr.File(label="Subir Listado (.xlsx)", type="filepath")
                            btn_analizar_excel = gr.Button("1. Analizar", variant="secondary")
                            tabla_preview = gr.Dataframe(headers=["Nº Serie Exacta", "Código Final", "Nombres", "Cargo", "Departamento", "Validación"], interactive=True, wrap=True)
                            btn_confirmar_masivo = gr.Button("2. Confirmar y Asignar", variant="primary")
                            mensaje_masivo = gr.Textbox(label="Resultado", interactive=False)

                    with gr.Group():
                        gr.Markdown("#### ⚙️ Mantenimiento del Inventario")
                        with gr.Accordion("6. Edición Masiva y Limpieza", open=False):
                            gr.Markdown("**A. Liberar Equipo ('Sin Asignar'):**")
                            with gr.Row():
                                serie_liberar = gr.Dropdown(label="Selecciona Equipo Ocupado", choices=[], interactive=True)
                                btn_liberar = gr.Button("🔓 Liberar Equipo", variant="stop")
                            mensaje_liberar = gr.Textbox(show_label=False, interactive=False)
                            
                            gr.Markdown("---")
                            gr.Markdown("**A.1 Dar de Baja / Eliminar Equipo (Solo LIBRES):**")
                            with gr.Row():
                                serie_eliminar_equipo = gr.Dropdown(label="Selecciona Equipo Libre a Eliminar", choices=[], interactive=True)
                                motivo_eliminar_equipo = gr.Textbox(label="Motivo (Obligatorio)", placeholder="Ej: Obsoleto")
                                btn_eliminar_equipo = gr.Button("🗑️ Eliminar / Dar de Baja", variant="stop")
                            mensaje_eliminar_equipo = gr.Textbox(show_label=False, interactive=False)

                            gr.Markdown("---")
                            gr.Markdown("**B. Edición en Tabla y Duplicados:**")
                            with gr.Row():
                                txt_buscar_edicion = gr.Textbox(label="Buscar equipo a editar")
                                btn_buscar_edicion = gr.Button("🔍 Buscar", variant="secondary")
                                btn_buscar_duplicados = gr.Button("⚠️ Duplicados", variant="stop")
                            tabla_edicion = gr.Dataframe(headers=["ID BD", "Nº Serie", "Tipo", "Marca", "Modelo", "Custodio Asignado", "Área/Dirección", "Estado", "Proveedor", "Nombre Comercial", "Observaciones", "Acción"], interactive=True, wrap=True)
                            btn_guardar_edicion_tabla = gr.Button("💾 Confirmar Cambios", variant="primary")
                            mensaje_edicion_tabla = gr.Textbox(label="Estado", interactive=False)
                            
                            gr.Markdown("---")
                            gr.Markdown("**C. Edición Rápida (Formulario):**")
                            serie_editar = gr.Dropdown(label="Selecciona Cualquier Equipo", choices=[])
                            serie_nueva_editar = gr.Textbox(label="Número de Serie")
                            with gr.Row():
                                marca_editar = gr.Textbox(label="Marca")
                                modelo_editar = gr.Textbox(label="Modelo")
                            tipo_editar = gr.Textbox(label="Tipo de Equipo")
                            with gr.Row():
                                estado_editar = gr.Dropdown(label="Estado", choices=["Operativo", "En Mantenimiento", "De Baja", "Dañado", "Eliminado"])
                                custodio_editar = gr.Dropdown(label="Custodio Asignado", choices=[], interactive=True, allow_custom_value=True)
                            proveedor_editar = gr.Textbox(label="Proveedor (Razón Social)")
                            nombre_comercial_editar = gr.Textbox(label="Nombre Comercial")
                            observaciones_editar = gr.Textbox(label="Observaciones del Equipo")
                            btn_guardar_edicion = gr.Button("💾 Guardar Cambios", variant="primary")
                            mensaje_edicion = gr.Textbox(show_label=False, interactive=False)

                with gr.Column(scale=2):
                    with gr.Group():
                        gr.Markdown("### 🔍 Buscador Universal y Visor de Inventario")
                        with gr.Row():
                            caja_busqueda = gr.Textbox(label="Buscar por: Serie, Código, Nombre, Orden o Proveedor", scale=4)
                            btn_buscar = gr.Button("🔎 Buscar", variant="primary", scale=1)
                            btn_sincronizar = gr.Button("🔄 Mostrar Todos", scale=1)
                        tabla_inventario = gr.Dataframe(headers=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"], interactive=False, wrap=True)

                    with gr.Group():
                        with gr.Accordion("🖨️ Generar Reporte Personalizado", open=False):
                            with gr.Row():
                                rep_orden = gr.Textbox(label="Filtrar por Orden o Proceso")
                                rep_custodio = gr.Textbox(label="Filtrar por Custodio o Área")
                            rep_columnas = gr.CheckboxGroup(choices=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"], value=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Nombre Comercial"], label="Columnas a Imprimir")
                            with gr.Row():
                                btn_rep_pdf = gr.Button("📄 Descargar PDF", variant="primary")
                                btn_rep_excel = gr.Button("📊 Descargar Excel", variant="secondary")
                            rep_mensaje = gr.Textbox(show_label=False, interactive=False)
                            rep_archivo = gr.File(label="Archivo Reporte", visible=False)

        with gr.TabItem("3. Gestión de Mantenimientos"):
            gr.Markdown("### 🛠️ Registro Técnico y Generación de Reportes de Mantenimiento")
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### 📝 Detalles de la Intervención")
                    custodio_mantenimiento = gr.Dropdown(label="1. Funcionario / Ubicación", choices=[], interactive=True)
                    serie_mantenimiento = gr.Dropdown(label="2. Equipo(s) a intervenir", choices=[], interactive=True, multiselect=True)
                    
                    with gr.Row():
                        fecha_mantenimiento = gr.Textbox(label="3. Fecha", elem_classes="fecha-calendario", lines=1, max_lines=1)
                        proximo_mantenimiento = gr.Textbox(label="4. Próximo Mantenimiento (Opcional)", elem_classes="fecha-calendario", lines=1, max_lines=1)

                    tipo_mantenimiento = gr.Dropdown(label="5. Tipo de Intervención", choices=["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"])
                    tecnico_proveedor = gr.Dropdown(label="6. Empresa / Técnico Asignado", choices=[], interactive=True, allow_custom_value=True)

                    gr.Markdown("---")
                    desc_checks = gr.Dropdown(label="7. Trabajos Realizados", choices=["Limpieza Interna (Sopleteado/Brocha)", "Reemplazo de Componentes/Periféricos", "Actualización de Software/Drivers", "Formateo y Reinstalación de SO", "Revisión de Fuente/Voltajes", "Limpieza Lógica de Virus"], multiselect=True, allow_custom_value=True)
                    desc_mantenimiento = gr.Textbox(label="Detalles adicionales / Observaciones", lines=2)
                    costo_mantenimiento = gr.Number(label="8. Costo Asociado ($)", value=0.0)

                    gr.Markdown("---")
                    with gr.Row():
                        fotos_mantenimiento = gr.File(label="9. Evidencia (Subir Archivos)", file_count="multiple", file_types=["image"], type="filepath")
                        foto_camara = gr.Image(label="📸 Tomar Foto Rápida (Cámara del dispositivo)", sources=["webcam", "upload"], type="filepath")

                    gr.Markdown("---")
                    gr.Markdown("#### ✍️ Confirmar Nombres para las Firmas")
                    nombre_admin_firma = gr.Textbox(label="Nombre: Administrador de la Orden de Compra")
                    nombre_funcionario_firma = gr.Textbox(label="Nombre: Funcionario a Cargo del Equipo")
                    nombre_tecnico_firma = gr.Textbox(label="Nombre: Técnico del Proveedor")

                    btn_guardar_mantenimiento = gr.Button("💾 Registrar y Generar Reporte", variant="primary")
                    msg_mantenimiento = gr.Textbox(show_label=False, interactive=False)
                    archivo_acta_descarga = gr.File(label="📄 Descargar Reporte Generado", visible=False)

                    gr.Markdown("---")
                    gr.Markdown("#### 🗑️📄 Gestionar Registros del Funcionario Seleccionado")
                    gr.Markdown("*(Se basa en el Funcionario/Ubicación elegido en el campo 1 de arriba)*")
                    registro_eliminar = gr.Dropdown(label="Selecciona Registro (Reporte)", choices=[], interactive=True)
                    with gr.Row():
                        btn_descargar_reporte = gr.Button("📥 Descargar Reporte PDF", variant="secondary")
                        btn_eliminar_mant = gr.Button("🗑️ Eliminar Registro", variant="stop")
                    archivo_reporte_descarga = gr.File(label="📄 Reporte Descargado", visible=False)
                    msg_eliminar = gr.Textbox(show_label=False, interactive=False)

                    with gr.Accordion("✏️ Editar Registro Seleccionado (Planificación)", open=False):
                        gr.Markdown("*(Nota: Actualiza los datos en la base y el historial, pero no modifica el PDF original ya firmado).*")
                        with gr.Row():
                            edit_mant_fecha = gr.Textbox(label="Fecha", elem_classes="fecha-calendario")
                            edit_mant_proximo = gr.Textbox(label="Próximo Mantenimiento", elem_classes="fecha-calendario")
                        edit_mant_tipo = gr.Dropdown(label="Tipo de Intervención", choices=["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"])
                        edit_mant_tecnico = gr.Textbox(label="Técnico / Proveedor")
                        edit_mant_desc = gr.Textbox(label="Descripción / Trabajos", lines=3)
                        edit_mant_costo = gr.Number(label="Costo ($)", value=0.0)
                        btn_guardar_edicion_mant = gr.Button("💾 Guardar Cambios en el Registro", variant="primary")
                        msg_edicion_mant = gr.Textbox(show_label=False, interactive=False)

                with gr.Column(scale=2):
                    gr.Markdown("#### 📋 Historial Técnico")
                    gr.Markdown("*(Se actualiza automáticamente al seleccionar el Funcionario/Ubicación)*")
                    tabla_mantenimientos = gr.Dataframe(headers=["Fecha", "Tipo", "Responsable", "Equipo(s) Intervenido(s)", "Descripción", "Costo", "Próxima Revisión"], interactive=False, wrap=True)

        with gr.TabItem("4. Auditoría de Sistema"):
            gr.Markdown("### 🕵️‍♂️ Bitácora Histórica (Inalterable)")
            btn_refrescar_auditoria = gr.Button("🔄 Refrescar Registro")
            tabla_auditoria = gr.Dataframe(headers=["Fecha y Hora", "Usuario", "Acción Ejecutada"], interactive=False, wrap=True)

        with gr.TabItem("5. Centro de Seguridad y Accesos"):
            panel_denegado = gr.Group(visible=False)
            with panel_denegado:
                gr.Markdown("### 🚫 Acceso Denegado")
                gr.Markdown("No tienes los privilegios de Administrador necesarios para ver ni modificar este módulo de seguridad.")

            panel_seguridad = gr.Group(visible=False)
            with panel_seguridad:
                gr.Markdown("### 🔐 Gestión de Administradores de la Plataforma")
                with gr.Row():
                    with gr.Column():
                        gr.Markdown("#### 👥 Usuarios Autorizados (Clic en la fila para editar)")
                        btn_refrescar_usuarios = gr.Button("🔄 Cargar Usuarios")
                        tabla_usuarios = gr.Dataframe(headers=["Nombre de Usuario", "Rol en el Sistema"], interactive=False, wrap=True)
                    
                    with gr.Column():
                        gr.Markdown("#### 🔍 Ver o Editar Usuario Existente:")
                        buscar_usuario_combo = gr.Dropdown(label="Buscar Usuario Registrado", choices=[], interactive=True)
                        gr.Markdown("---")
                        usuario_db_in = gr.Textbox(label="Usuario a Editar / Crear")
                        rol_db_in = gr.Dropdown(label="Rol Asignado", choices=["admin", "operador"], value="admin")
                        clave_db_in = gr.Textbox(label="Contraseña (Escribe una nueva para actualizar, o déjalo vacío para solo cambiar el rol)", type="password")
                        with gr.Row():
                            btn_reset_clave = gr.Button("💾 Guardar / Actualizar", variant="primary")
                            btn_limpiar_usr = gr.Button("🧹 Limpiar", variant="secondary")
                        mensaje_clave = gr.Textbox(show_label=False, interactive=False)
                
                gr.Markdown("---")
                gr.Markdown("### 💾 Respaldo de Base de Datos (Backup)")
                gr.Markdown("Genera descargas periódicas de seguridad de toda la base de datos (JSON) presionando este botón:")
                with gr.Row():
                    btn_respaldo = gr.Button("⬇️ Generar y Descargar Respaldo Manual", variant="primary")
                with gr.Row():
                    msg_respaldo = gr.Textbox(show_label=False, interactive=False)
                    archivo_respaldo = gr.File(label="Archivo de Respaldo Generado (JSON)", visible=False)
                
                gr.Markdown("---")
                gr.Markdown("### ♻️ Restaurar Sistema desde Respaldo")
                gr.Markdown("*(Sube un archivo `.json` de respaldo previo para restaurar todos los datos)*")
                with gr.Row():
                    archivo_subir_respaldo = gr.File(label="Seleccionar Archivo de Respaldo (.json)", file_types=[".json"])
                    btn_restaurar = gr.Button("🔄 Cargar y Restaurar Datos", variant="primary")
                msg_restaurar = gr.Textbox(show_label=False, interactive=False)

    # Agrupamos TODOS los eventos al final para evitar errores de tipo NameError (componentes no definidos).

    # Eventos Pestaña 1
    btn_analizar.click(fn=analizar_fase_precontractual, inputs=[archivos_input, referencias_input, enlaces_input], outputs=reporte_output)

    # Eventos Pestaña 2
    btn_logout.click(fn=None, inputs=None, outputs=None, js="() => { window.location.href = '/logout'; }")
    btn_analizar_acta.click(fn=analizar_acta_pdf, inputs=[acta_input], outputs=[tabla_preview_acta, datos_acta_state, mensaje_acta])
    btn_procesar_acta.click(fn=procesar_acta_recepcion, inputs=[tabla_preview_acta, datos_acta_state], outputs=[mensaje_acta, tabla_inventario, serie_asignar])
    btn_guardar_manual.click(fn=ingresar_equipo_manual, inputs=[num_proceso_man, num_orden_man, proveedor_man, nombre_comercial_man, objeto_man, monto_man, tipo_man, marca_man, modelo_man, serie_man, observaciones_man], outputs=[mensaje_manual, tabla_inventario, serie_asignar])
    
    tabla_funcionarios.select(fn=seleccionar_funcionario_tabla, inputs=[tabla_funcionarios], outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo])
    buscar_func_combo.change(fn=cargar_datos_funcionario_form, inputs=[buscar_func_combo], outputs=[cedula_in, nombres_in, cargo_in, depto_in])
    btn_guardar_func.click(fn=registrar_y_actualizar, inputs=[cedula_in, nombres_in, cargo_in, depto_in], outputs=[mensaje_func, custodio_asignar, tabla_funcionarios, buscar_func_combo])
    btn_limpiar_func.click(fn=lambda: ("", "", "", "", gr.update(value=None)), inputs=[], outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo])
    btn_eliminar_func.click(fn=eliminar_funcionario, inputs=[cedula_in], outputs=[mensaje_func, custodio_asignar, tabla_funcionarios, buscar_func_combo]).then(fn=lambda: ("", "", "", "", gr.update(value=None)), inputs=[], outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo])

    custodio_asignar.change(fn=advertencia_asignacion, inputs=[custodio_asignar], outputs=[alerta_asignacion])
    btn_asignar.click(fn=asignar_custodio_equipo, inputs=[serie_asignar, custodio_asignar, garantia_input], outputs=[mensaje_asignar, tabla_inventario, serie_asignar, custodio_asignar, serie_liberar])
    btn_analizar_excel.click(fn=analizar_excel_masivo, inputs=[excel_input], outputs=[mensaje_masivo, tabla_preview])
    
    # ESTOS ERAN LOS BOTONES QUE CAUSABAN EL NameError
    btn_confirmar_masivo.click(fn=confirmar_asignacion_masiva, inputs=[tabla_preview], outputs=[mensaje_masivo]).then(fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento])
    btn_sincronizar.click(fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento])

    tabla_inventario.select(fn=seleccionar_inventario_tabla, inputs=[tabla_inventario], outputs=[serie_editar, estado_editar, tipo_editar, serie_liberar])
    btn_buscar.click(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    caja_busqueda.submit(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    
    btn_liberar.click(fn=liberar_equipo, inputs=[serie_liberar], outputs=[mensaje_liberar, tabla_inventario, serie_asignar, serie_liberar])
    btn_eliminar_equipo.click(fn=eliminar_equipo_inventario, inputs=[serie_eliminar_equipo, motivo_eliminar_equipo], outputs=[mensaje_eliminar_equipo, tabla_inventario, serie_asignar, serie_liberar, serie_eliminar_equipo, tabla_auditoria, motivo_eliminar_equipo]).then(fn=obtener_series_disponibles, inputs=[], outputs=[serie_eliminar_equipo])

    btn_buscar_edicion.click(fn=cargar_tabla_edicion, inputs=[txt_buscar_edicion], outputs=[tabla_edicion])
    btn_buscar_duplicados.click(fn=cargar_duplicados, inputs=[], outputs=[tabla_edicion])
    btn_guardar_edicion_tabla.click(fn=confirmar_edicion_masiva, inputs=[tabla_edicion], outputs=[mensaje_edicion_tabla]).then(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    
    serie_editar.change(fn=cargar_equipo_para_edicion, inputs=[serie_editar], outputs=[serie_nueva_editar, marca_editar, modelo_editar, estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar, custodio_editar])
    btn_guardar_edicion.click(fn=guardar_edicion_equipo, inputs=[serie_editar, serie_nueva_editar, marca_editar, modelo_editar, estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar, custodio_editar], outputs=[mensaje_edicion]).then(fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento]).then(fn=obtener_funcionarios_mantenimiento, inputs=[], outputs=[custodio_editar])

    btn_rep_pdf.click(fn=exportar_pdf_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])
    btn_rep_excel.click(fn=exportar_excel_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])
    
    # Eventos Pestaña 3
    custodio_mantenimiento.change(fn=cargar_equipos_de_funcionario, inputs=[custodio_mantenimiento], outputs=[serie_mantenimiento, nombre_funcionario_firma]).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos]).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar])
    
    serie_mantenimiento.change(fn=auto_completar_mantenimiento, inputs=[serie_mantenimiento], outputs=[tecnico_proveedor])
    
    btn_guardar_mantenimiento.click(fn=registrar_y_generar_acta, inputs=[custodio_mantenimiento, serie_mantenimiento, fecha_mantenimiento, tipo_mantenimiento, desc_checks, desc_mantenimiento, tecnico_proveedor, costo_mantenimiento, proximo_mantenimiento, fotos_mantenimiento, foto_camara, nombre_admin_firma, nombre_tecnico_firma, nombre_funcionario_firma], outputs=[msg_mantenimiento, tabla_mantenimientos, archivo_acta_descarga]).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar])
    
    btn_descargar_reporte.click(fn=descargar_reporte_mantenimiento, inputs=[registro_eliminar], outputs=[archivo_reporte_descarga, msg_eliminar])
    btn_eliminar_mant.click(fn=eliminar_mantenimiento, inputs=[registro_eliminar], outputs=[msg_eliminar]).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar]).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos])

    registro_eliminar.change(fn=cargar_datos_mantenimiento_edicion, inputs=[registro_eliminar], outputs=[edit_mant_fecha, edit_mant_proximo, edit_mant_tipo, edit_mant_tecnico, edit_mant_desc, edit_mant_costo, visor_pdf_edicion])
    
    btn_guardar_edicion_mant.click(fn=guardar_edicion_mantenimiento, inputs=[registro_eliminar, edit_mant_fecha, edit_mant_tipo, edit_mant_tecnico, edit_mant_desc, edit_mant_costo, edit_mant_proximo], outputs=[msg_edicion_mant]).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos])

    # Eventos Pestaña 4
    btn_refrescar_auditoria.click(fn=cargar_datos_auditoria, inputs=[], outputs=tabla_auditoria)

    # Eventos Pestaña 5
    btn_respaldo.click(fn=generar_respaldo_manual, inputs=[], outputs=[archivo_respaldo, msg_respaldo])
    btn_restaurar.click(fn=restaurar_base_datos, inputs=[archivo_subir_respaldo], outputs=[msg_restaurar]).then(fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento])

    tabla_usuarios.select(fn=seleccionar_usuario_tabla, inputs=[tabla_usuarios], outputs=[usuario_db_in, rol_db_in, buscar_usuario_combo])
    buscar_usuario_combo.change(fn=cargar_datos_usuario_form, inputs=[buscar_usuario_combo], outputs=[usuario_db_in, rol_db_in])
    btn_limpiar_usr.click(fn=lambda: ("", "admin", "", gr.update(value=None)), inputs=[], outputs=[usuario_db_in, rol_db_in, clave_db_in, buscar_usuario_combo])
    btn_refrescar_usuarios.click(fn=cargar_usuarios_sistema, inputs=[], outputs=[tabla_usuarios])
    btn_reset_clave.click(fn=gestionar_usuario_admin, inputs=[usuario_db_in, clave_db_in, rol_db_in], outputs=[mensaje_clave, tabla_usuarios, buscar_usuario_combo])

    script_calendario = """
    () => {
        setInterval(() => {
            document.querySelectorAll('.fecha-calendario input').forEach(el => {
                if (el.type !== 'date') el.type = 'date';
            });
            let app = document.querySelector('gradio-app');
            if (app && app.shadowRoot) {
                app.shadowRoot.querySelectorAll('.fecha-calendario input').forEach(el => {
                    if (el.type !== 'date') el.type = 'date';
                });
            }
        }, 500);
    }
    """
    
    erp_interfaz.load(
        fn=inicializar_sistema_completo, 
        inputs=[], 
        outputs=[
            tabla_auditoria, tabla_usuarios, tabla_inventario, 
            serie_asignar, custodio_asignar, serie_liberar, 
            serie_editar, tabla_funcionarios, buscar_func_combo, 
            buscar_usuario_combo, custodio_mantenimiento, panel_seguridad, panel_denegado
        ],
        js=script_calendario
    ).then(fn=obtener_series_disponibles, inputs=[], outputs=[serie_eliminar_equipo])

import fastapi
app = fastapi.FastAPI()
app = gr.mount_gradio_app(app, erp_interfaz, path="/", auth=verificar_credenciales)

if __name__ == "__main__":
    import uvicorn
    # Cambiamos el puerto local a 8000 para evitar el choque con el proceso "fantasma"
    uvicorn.run(app, host="0.0.0.0", port=8000)
