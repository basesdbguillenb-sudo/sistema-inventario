import gradio as gr
import pandas as pd
from fpdf import FPDF
import json
import os
import re
import tempfile
import time
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from pypdf import PdfReader
from io import BytesIO
from PIL import Image
from google import genai  # ACTUALIZADO: Nueva librería oficial de Gemini

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    print(f"Error al conectar con Supabase: {e}")

# ACTUALIZADO: Nuevo cliente de Gemini
if GEMINI_API_KEY:
    client = genai.Client(api_key=GEMINI_API_KEY)
else:
    client = None

def verificar_credenciales(usuario, clave):
    try:
        res = supabase.table("usuarios_sistema").select("clave").eq("usuario", usuario).execute()
        if res.data and res.data[0]['clave'] == clave: return True
        return False
    except: return False

def registrar_auditoria(usuario, accion):
    try:
        supabase.table("auditoria").insert({"usuario": usuario, "accion": accion}).execute()
    except Exception as e: print(f"Error auditoría: {e}")

# ACTUALIZADO: Uso del nuevo cliente de Google GenAI
def llamar_gemini_con_reintentos(prompt, max_retries=3, delay=2):
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model='gemini-2.5-flash',
                contents=prompt
            )
        except Exception as e:
            if "429" in str(e) and attempt < max_retries - 1:
                time.sleep(delay)
            else:
                raise e

def cargar_datos_auditoria():
    try:
        res = supabase.table("auditoria").select("*").order("fecha", desc=True).limit(100).execute()
        if not res.data: return [["-", "-", "-"]]
        return [[r.get('fecha', '').split('.')[0].replace('T', ' '), r.get('usuario', ''), r.get('accion', '')] for r in res.data]
    except Exception as e: return [[f"Error: {e}", "-", "-"]]

def cargar_usuarios_sistema():
    try:
        res = supabase.table("usuarios_sistema").select("usuario, rol").execute()
        return [[r['usuario'], r['rol']] for r in res.data] if res.data else [["-", "-"]]
    except Exception as e: return [[f"Error: {e}", "-"]]

def cargar_datos_inventario(termino=""):
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial, numero_orden_compra, numero_proceso_sercop), funcionarios(nombres_completos, departamento)").execute()
        datos = []
        for eq in res.data:
            ord_data = eq.get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            elif not isinstance(ord_data, dict): ord_data = {}
            
            prov = ord_data.get("razon_social_proveedor", "")
            nomc = ord_data.get("nombre_comercial", "")
            n_ord = ord_data.get("numero_orden_compra", "Sin CE")
            n_proc = ord_data.get("numero_proceso_sercop", "Sin Proceso")

            func = eq.get("funcionarios")
            cust = func.get("nombres_completos") if func else "Sin Asignar"
            dept = func.get("departamento") if func else "-"
            
            obs = eq.get("observaciones", "") or ""

            eq_str = f"{eq.get('tipo_equipo','')} {eq.get('marca','')} {eq.get('modelo','')}".strip()
            f_str = f"{eq.get('numero_serie','')} {eq_str} {prov} {nomc} {n_ord} {n_proc} {cust}".lower()
            
            if termino and termino.lower() not in f_str: continue

            datos.append([eq_str, eq.get("numero_serie",""), cust, dept, eq.get("estado",""), n_ord, n_proc, prov, nomc, obs])
        return datos if datos else [["-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def obtener_series_disponibles():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").is_("funcionario_id", "null").execute()
        return gr.update(choices=[f"{e['numero_serie']} - {e.get('tipo_equipo','')} {e.get('marca','')}".strip() for e in res.data]) if res.data else gr.update(choices=[])
    except: return gr.update(choices=[])

def obtener_series_asignadas():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca, funcionarios(nombres_completos)").not_.is_("funcionario_id", "null").execute()
        return gr.update(choices=[f"{e['numero_serie']} - {e.get('funcionarios', {}).get('nombres_completos', 'Desconocido')} ({e.get('tipo_equipo','')})".strip() for e in res.data]) if res.data else gr.update(choices=[])
    except: return gr.update(choices=[])

def obtener_todas_las_series():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").execute()
        return gr.update(choices=[f"{e['numero_serie']} - {e.get('tipo_equipo','')} {e.get('marca','')}".strip() for e in res.data]) if res.data else gr.update(choices=[])
    except: return gr.update(choices=[])

def obtener_funcionarios():
    try:
        res = supabase.table("funcionarios").select("nombres_completos, cedula").execute()
        return gr.update(choices=[f"{f['nombres_completos']} - {f['cedula']}" for f in res.data]) if res.data else gr.update(choices=[])
    except: return gr.update(choices=[])

def cargar_listado_funcionarios():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos, cargo, departamento").execute()
        return [[r['cedula'], r['nombres_completos'], r.get('cargo',''), r.get('departamento','')] for r in res.data] if res.data else [["-", "-", "-", "-"]]
    except: return [["-", "-", "-", "-"]]

def realizar_busqueda(termino): return cargar_datos_inventario(termino)

def obtener_usuarios_lista():
    try:
        res = supabase.table("usuarios_sistema").select("usuario").execute()
        return gr.update(choices=[f"{r['usuario']} - admin" for r in res.data]) if res.data else gr.update(choices=[])
    except: return gr.update(choices=[])

def obtener_funcionarios_mantenimiento():
    try:
        res = supabase.table("funcionarios").select("id, nombres_completos, departamento").execute()
        choices = [f"{f['nombres_completos']} - {f.get('departamento','')}" for f in res.data] if res.data else []
        return gr.update(choices=choices)
    except: return gr.update(choices=[])

def cargar_equipos_de_funcionario(funcionario_str):
    if not funcionario_str: return gr.update(choices=[], value=[]), ""
    nombre = funcionario_str.split(" - ")[0].strip()
    try:
        res_f = supabase.table("funcionarios").select("id").eq("nombres_completos", nombre).execute()
        if not res_f.data: return gr.update(choices=[], value=[]), nombre
        f_id = res_f.data[0]['id']
        res_e = supabase.table("equipos").select("numero_serie, tipo_equipo, marca, modelo").eq("funcionario_id", f_id).execute()
        choices = [f"{e['numero_serie']} - {e.get('tipo_equipo','')} {e.get('marca','')} {e.get('modelo','')}" for e in res_e.data]
        return gr.update(choices=choices, value=[]), nombre
    except: return gr.update(choices=[], value=[]), nombre

def cargar_historial_por_funcionario(funcionario_str):
    if not funcionario_str: return [["-", "-", "-", "-", "-", "-", "-"]]
    nombre = funcionario_str.split(" - ")[0].strip()
    try:
        res_f = supabase.table("funcionarios").select("id").eq("nombres_completos", nombre).execute()
        if not res_f.data: return [["-", "-", "-", "-", "-", "-", "-"]]
        f_id = res_f.data[0]['id']
        res_m = supabase.table("mantenimientos").select("*").eq("funcionario_id", f_id).order("fecha", desc=True).execute()
        if not res_m.data: return [["-", "Sin historial", "-", "-", "-", "-", "-"]]
        datos = []
        for m in res_m.data:
            datos.append([
                m.get("fecha", ""), m.get("tipo", ""), m.get("tecnico", ""), 
                m.get("equipos_afectados", ""), m.get("descripcion", ""), 
                f"${m.get('costo', 0.0)}", m.get("proximo_mantenimiento", "")
            ])
        return datos
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-"]]

def auto_completar_mantenimiento(equipos_seleccionados):
    if not equipos_seleccionados:
        return gr.update(value=""), gr.update(value=[["-", "-", "-", "-", "-", "-", "-"]])
        
    try:
        serie = equipos_seleccionados[0].split(" - ")[0].strip()
        res_eq = supabase.table("equipos").select("orden_id, ordenes_compra(razon_social_proveedor)").eq("numero_serie", serie).execute()
        if res_eq.data:
            ord_data = res_eq.data[0].get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            elif not isinstance(ord_data, dict): ord_data = {}
            prov = ord_data.get("razon_social_proveedor", "")
            return gr.update(value=prov), gr.update()
    except: pass
    return gr.update(), gr.update()

def acumular_foto_camara(foto_actual, lista_estado):
    if foto_actual is None:
        return lista_estado, lista_estado, None
    if lista_estado is None:
        lista_estado = []
    lista_estado.append(foto_actual)
    return lista_estado, lista_estado, None

def limpiar_galeria():
    return [], [], None

def registrar_y_generar_acta(funcionario_combo, serie_combo, fecha, tipo, checks, desc_extra, tecnico, costo, proximo, fotos_paths, estado_fotos_camara, nombre_admin, nombre_tecnico, nombre_func, plantilla_logico, plantilla_fisico, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not funcionario_combo or not serie_combo or not fecha or not tipo: 
        return "⚠️ Faltan datos obligatorios.", [["-", "-", "-", "-", "-", "-", "-"]], gr.update(visible=False, value=None), [], []

    if fotos_paths is None:
        fotos_paths = []
    elif not isinstance(fotos_paths, list):
        fotos_paths = [fotos_paths]
        
    if estado_fotos_camara:
        if isinstance(estado_fotos_camara, list): fotos_paths.extend(estado_fotos_camara)
        else: fotos_paths.append(estado_fotos_camara)

    nombre_custodio = str(funcionario_combo).split(" - ")[0].strip() if funcionario_combo else "Sin Asignar"
    
    # MAGIA DE REEMPLAZO DINÁMICO
    textos_trabajos = []
    if checks:
        for c in checks:
            if c == "Mantenimiento Lógico":
                textos_trabajos.append(f"MANTENIMIENTO LÓGICO:\n{plantilla_logico}")
            elif c == "Mantenimiento Físico":
                textos_trabajos.append(f"MANTENIMIENTO FÍSICO:\n{plantilla_fisico}")
            else:
                textos_trabajos.append(f"  - {c}")
                
    trabajos_str = "\n\n".join(textos_trabajos)
    if trabajos_str and desc_extra:
        desc_final = f"{trabajos_str}\n\nOBSERVACIONES:\n{desc_extra}"
    else:
        desc_final = trabajos_str or desc_extra or "Revisión general."
        
    equipos_str = ", ".join([s.split(" - ")[0].strip() for s in serie_combo])
    
    try:
        res_f = supabase.table("funcionarios").select("id, departamento").eq("nombres_completos", nombre_custodio).execute()
        f_id = res_f.data[0]['id'] if res_f.data else None
        depto_custodio = res_f.data[0].get('departamento', 'Sin Área') if res_f.data else 'Sin Área'
        
        datos_mant = {
            "funcionario_id": f_id, "fecha": fecha, "tipo": tipo,
            "tecnico": tecnico, "equipos_afectados": equipos_str,
            "descripcion": desc_final, "costo": float(costo or 0),
            "proximo_mantenimiento": proximo
        }
        res_ins = supabase.table("mantenimientos").insert(datos_mant).execute()
        mant_id = res_ins.data[0]['id'] if res_ins.data else "TEMP"
        
        pdf = FPDF()
        pdf.add_page()
        
        def l(txt): return str(txt).encode('latin-1', 'replace').decode('latin-1')

        pdf.set_font("Arial", 'B', 16)
        pdf.cell(0, 10, l("REPORTE TÉCNICO DE MANTENIMIENTO"), ln=True, align='C')
        pdf.set_font("Arial", '', 10)
        pdf.cell(0, 8, l(f"Fecha de Intervención: {fecha} | ID Registro: {mant_id}"), ln=True, align='C')
        pdf.ln(5)

        pdf.set_font("Arial", 'B', 12)
        pdf.set_fill_color(230, 230, 230)
        pdf.cell(0, 8, l("1. DATOS DE LA UBICACIÓN Y RESPONSABLE"), 1, 1, 'L', fill=True)
        pdf.set_font("Arial", '', 10)
        pdf.cell(40, 8, l("Funcionario/Área:"), 1)
        pdf.cell(150, 8, l(f"{nombre_custodio} - {depto_custodio}"), 1, 1)
        pdf.cell(40, 8, l("Técnico/Empresa:"), 1)
        pdf.cell(150, 8, l(tecnico or "No especificado"), 1, 1)

        pdf.ln(5)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, l("2. DETALLE DE EQUIPOS INTERVENIDOS"), 1, 1, 'L', fill=True)
        pdf.set_font("Arial", '', 10)
        pdf.multi_cell(0, 8, l(equipos_str), 1)

        pdf.ln(5)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, l("3. DESCRIPCIÓN DEL TRABAJO REALIZADO"), 1, 1, 'L', fill=True)
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 6, l(f"Tipo de Mantenimiento: {tipo}"), 0, 1)
        pdf.set_font("Arial", '', 10)
        pdf.multi_cell(0, 6, l(desc_final), 1)

        pdf.ln(5)
        pdf.set_font("Arial", 'B', 12)
        pdf.cell(0, 8, l("4. COSTOS Y PLANIFICACIÓN"), 1, 1, 'L', fill=True)
        pdf.set_font("Arial", '', 10)
        pdf.cell(60, 8, l("Costo de la Intervención:"), 1)
        pdf.cell(130, 8, l(f"${float(costo or 0):.2f}"), 1, 1)
        pdf.cell(60, 8, l("Próximo Mantenimiento:"), 1)
        pdf.cell(130, 8, l(proximo or "No definido"), 1, 1)

        pdf.ln(15)
        pdf.set_font("Arial", 'B', 10)
        pdf.cell(0, 6, l("FIRMAS DE CONFORMIDAD Y RECEPCIÓN"), 0, 1, 'C')
        pdf.ln(15)

        w = 60
        pdf.cell(w, 4, l("_________________________"), 0, 0, 'C')
        pdf.cell(w, 4, l("_________________________"), 0, 0, 'C')
        pdf.cell(w, 4, l("_________________________"), 0, 1, 'C')
        
        pdf.set_font("Arial", '', 8)
        pdf.cell(w, 4, l((nombre_admin or "Administrador").strip()), 0, 0, 'C')
        pdf.cell(w, 4, l((nombre_funcionario_firma or nombre_custodio).strip()), 0, 0, 'C')
        pdf.cell(w, 4, l((nombre_tecnico or tecnico or "Técnico").strip()), 0, 1, 'C')
        
        pdf.cell(w, 4, l("Admin. Orden de Compra"), 0, 0, 'C')
        pdf.cell(w, 4, l("Funcionario a Cargo"), 0, 0, 'C')
        pdf.cell(w, 4, l("Técnico / Proveedor"), 0, 1, 'C')

        if fotos_paths and len(fotos_paths) > 0:
            pdf.ln(10)
            pdf.set_font("Arial", 'I', 9)
            pdf.cell(0, 6, l("Nota aclaratoria: Adjunto a este documento se encuentra el respaldo fotográfico del presente servicio."), 0, 1, 'C')

        if fotos_paths and len(fotos_paths) > 0:
            pdf.add_page()
            pdf.set_font("Arial", 'B', 14)
            pdf.cell(0, 10, l("REGISTRO FOTOGRÁFICO DE LA INTERVENCIÓN"), ln=True, align='C')
            pdf.ln(5)
            
            y_offset = pdf.get_y()
            max_img_width = 180
            max_img_height = 110 

            for idx, fp in enumerate(fotos_paths):
                try:
                    img = Image.open(fp)
                    if img.mode == 'RGBA':
                        img = img.convert('RGB')
                    
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp:
                        img.save(tmp.name, 'JPEG', quality=85)
                        tmp_path = tmp.name
                        
                    img_w, img_h = img.size
                    ratio = min(max_img_width/img_w, max_img_height/img_h)
                    new_w = img_w * ratio
                    new_h = img_h * ratio
                    
                    if y_offset + new_h > 280:
                        pdf.add_page()
                        y_offset = 20
                        
                    center_x = (210 - new_w) / 2
                    pdf.image(tmp_path, x=center_x, y=y_offset, w=new_w, h=new_h)
                    y_offset += new_h + 10
                    
                    pdf.set_y(y_offset)
                    pdf.set_font("Arial", 'I', 9)
                    pdf.cell(0, 5, l(f"Evidencia {idx+1}"), 0, 1, 'C')
                    y_offset += 10
                    
                    try: os.remove(tmp_path)
                    except: pass
                    
                except Exception as ex: print(f"Error insertando foto {fp}: {ex}")

        nombre_archivo = f"Reporte_Mantenimiento_{mant_id}.pdf"
        ruta_pdf = os.path.join(tempfile.gettempdir(), nombre_archivo)
        pdf.output(ruta_pdf)
        
        with open(ruta_pdf, "rb") as f:
            pdf_bytes = f.read()
        supabase.storage.from_("reportes").upload(f"mantenimientos/{nombre_archivo}", pdf_bytes, {"content-type": "application/pdf"})
        
        registrar_auditoria(usuario, f"Registró mantenimiento para {nombre_custodio} ({equipos_str}).")
        
        return "✅ Mantenimiento registrado y reporte PDF generado correctamente.", cargar_historial_por_funcionario(funcionario_combo), gr.update(value=ruta_pdf, visible=True), [], []
        
    except Exception as e: return f"❌ Error guardando mantenimiento: {e}", cargar_historial_por_funcionario(funcionario_combo), gr.update(visible=False, value=None), [], []

def obtener_mantenimientos_lista_por_funcionario(funcionario_combo):
    if not funcionario_combo: return gr.update(choices=[], value=None)
    nombre = funcionario_combo.split(" - ")[0].strip()
    try:
        res_f = supabase.table("funcionarios").select("id").eq("nombres_completos", nombre).execute()
        if not res_f.data: return gr.update(choices=[], value=None)
        f_id = res_f.data[0]['id']
        res_m = supabase.table("mantenimientos").select("id, fecha, tipo").eq("funcionario_id", f_id).order("fecha", desc=True).execute()
        if not res_m.data: return gr.update(choices=[], value=None)
        choices = [f"ID:{m['id']} - {m['fecha']} ({m['tipo']})" for m in res_m.data]
        return gr.update(choices=choices, value=None)
    except: return gr.update(choices=[], value=None)

def descargar_reporte_mantenimiento(registro_combo):
    if not registro_combo: return gr.update(visible=False, value=None), "⚠️ Selecciona un registro primero."
    try:
        mant_id = registro_combo.split(" - ")[0].replace("ID:", "").strip()
        nombre_archivo = f"Reporte_Mantenimiento_{mant_id}.pdf"
        res = supabase.storage.from_("reportes").download(f"mantenimientos/{nombre_archivo}")
        ruta_local = os.path.join(tempfile.gettempdir(), nombre_archivo)
        with open(ruta_local, "wb") as f: f.write(res)
        return gr.update(value=ruta_local, visible=True), f"✅ Reporte {mant_id} descargado."
    except Exception as e: return gr.update(visible=False, value=None), f"❌ Error o reporte no existe: {e}"

def eliminar_mantenimiento(registro_combo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not registro_combo: return "⚠️ Selecciona un registro para eliminar."
    try:
        mant_id = registro_combo.split(" - ")[0].replace("ID:", "").strip()
        supabase.table("mantenimientos").delete().eq("id", mant_id).execute()
        nombre_archivo = f"Reporte_Mantenimiento_{mant_id}.pdf"
        try: supabase.storage.from_("reportes").remove([f"mantenimientos/{nombre_archivo}"])
        except: pass
        registrar_auditoria(usuario, f"Eliminó mantenimiento ID: {mant_id}.")
        return f"🗑️ Mantenimiento {mant_id} eliminado de la base de datos y almacenamiento."
    except Exception as e: return f"❌ Error al eliminar: {e}"

def cargar_datos_mantenimiento_edicion(registro_combo):
    if not registro_combo: return "", "", "", "", "", 0.0, gr.update(visible=False, value=None)
    try:
        mant_id = registro_combo.split(" - ")[0].replace("ID:", "").strip()
        res = supabase.table("mantenimientos").select("*").eq("id", mant_id).execute()
        if not res.data: return "", "", "", "", "", 0.0, gr.update(visible=False, value=None)
        
        m = res.data[0]
        fecha = m.get("fecha", "")[:10] if m.get("fecha") else ""
        proximo = m.get("proximo_mantenimiento", "")[:10] if m.get("proximo_mantenimiento") else ""
        
        ruta_local = None
        try:
            nombre_archivo = f"Reporte_Mantenimiento_{mant_id}.pdf"
            res_pdf = supabase.storage.from_("reportes").download(f"mantenimientos/{nombre_archivo}")
            ruta_local = os.path.join(tempfile.gettempdir(), f"Visor_{nombre_archivo}")
            with open(ruta_local, "wb") as f: f.write(res_pdf)
            visor_update = gr.update(value=ruta_local, visible=True)
        except: visor_update = gr.update(visible=False, value=None)
        
        return fecha, proximo, m.get("tipo", ""), m.get("tecnico", ""), m.get("descripcion", ""), float(m.get("costo", 0.0)), visor_update
    except Exception as e: return "", "", "", "", "", 0.0, gr.update(visible=False, value=None)

def guardar_edicion_mantenimiento(registro_combo, fecha, proximo, tipo, tecnico, descripcion, costo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not registro_combo: return "⚠️ Selecciona un registro primero."
    try:
        mant_id = registro_combo.split(" - ")[0].replace("ID:", "").strip()
        datos_actualizar = {
            "fecha": fecha, "proximo_mantenimiento": proximo,
            "tipo": tipo, "tecnico": tecnico,
            "descripcion": descripcion, "costo": float(costo or 0)
        }
        supabase.table("mantenimientos").update(datos_actualizar).eq("id", mant_id).execute()
        registrar_auditoria(usuario, f"Editó planificación del mantenimiento ID: {mant_id}.")
        return f"✅ Registro {mant_id} actualizado correctamente en la base de datos."
    except Exception as e: return f"❌ Error al guardar edición: {e}"

def analizar_fase_precontractual(archivos_pdf, referencias_pdf, enlaces, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not archivos_pdf: return "Sube al menos un documento PDF (Pliego/TDR)."
    
    texto_doc = ""
    for arch in archivos_pdf:
        texto_doc += "".join(pagina.extract_text() + "\n" for pagina in PdfReader(arch).pages)
        
    texto_ref = ""
    if referencias_pdf:
        for arch in referencias_pdf:
            texto_ref += "".join(pagina.extract_text() + "\n" for pagina in PdfReader(arch).pages)
            
    prompt = f"""
    Actúa como un Auditor Experto en Contratación Pública (SERCOP Ecuador).
    Analiza este documento principal (Pliego/TDR):
    {texto_doc[:15000]}
    
    Toma en cuenta estas referencias normativas (si aplican):
    {texto_ref[:5000]}
    Y estos enlaces: {enlaces}
    
    Genera un informe estructurado con:
    1. Resumen Ejecutivo (Objeto, presupuesto, plazos).
    2. Alertas de Cumplimiento (Posibles violaciones a la ley).
    3. Evaluación de Riesgos y Recomendaciones técnicas.
    """
    try:
        respuesta = llamar_gemini_con_reintentos(prompt)
        registrar_auditoria(usuario, "Ejecutó análisis de fase precontractual.")
        return respuesta.text
    except Exception as e: return f"Error en IA: {e}"

def analizar_acta_pdf(archivo_pdf):
    if not archivo_pdf: return None, {}, "⚠️ Sube un PDF."
    try:
        texto = "".join(pagina.extract_text() + "\n" for pagina in PdfReader(archivo_pdf).pages)
        prompt = f"""
        Extrae los bienes de esta acta de entrega-recepción y devuelve SOLO un JSON estricto:
        {{
            "razon_social_proveedor": "Nombre de empresa o persona",
            "nombre_comercial": "Nombre comercial si existe",
            "numero_orden_compra": "CE-2023... o similar",
            "numero_proceso_sercop": "SIE-..., RE-... o similar",
            "objeto_contratacion": "Objeto del contrato",
            "monto": "Monto numérico",
            "equipos": [
                {{"tipo": "Laptop/PC/Monitor", "marca": "HP", "modelo": "X1", "serie": "123456", "observaciones": "..."}}
            ]
        }}
        Texto del acta: {texto[:10000]}
        """
        res_ia = llamar_gemini_con_reintentos(prompt)
        texto_json = res_ia.text.strip()
        for m in ["```json", "```"]: texto_json = texto_json.replace(m, "")
        datos = json.loads(texto_json.strip())
        
        filas = []
        for eq in datos.get("equipos", []):
            filas.append([
                eq.get("tipo", ""), eq.get("marca", ""), eq.get("modelo", ""), eq.get("serie", ""),
                datos.get("razon_social_proveedor", ""), datos.get("nombre_comercial", ""),
                datos.get("numero_orden_compra", ""), datos.get("numero_proceso_sercop", ""), eq.get("observaciones", "")
            ])
        return filas, datos, "✅ Acta leída. Edita en la tabla y Guarda."
    except Exception as e: return None, {}, f"❌ Error: {e}"

def procesar_acta_recepcion(tabla_datos, state_datos, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if tabla_datos is None or tabla_datos.empty: return "⚠️ Tabla vacía.", cargar_datos_inventario(), obtener_series_disponibles()
    try:
        proveedor_extraido = str(tabla_datos.iloc[0, 4]).strip()
        nombre_comercial_extraido = str(tabla_datos.iloc[0, 5]).strip()
        num_orden = str(tabla_datos.iloc[0, 6]).strip()
        num_proceso = str(tabla_datos.iloc[0, 7]).strip()
        
        resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", num_proceso).eq("numero_orden_compra", num_orden).execute()
        orden_id = None
        
        if not resp_orden.data:
            resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{num_proceso} [Ref: {num_orden}]").eq("numero_orden_compra", num_orden).execute()
        
        if resp_orden.data:
            orden_id = resp_orden.data[0]['id']
            supabase.table("ordenes_compra").update({
                "razon_social_proveedor": proveedor_extraido,
                "nombre_comercial": nombre_comercial_extraido
            }).eq("id", orden_id).execute()
        else:
            proceso_a_guardar = num_proceso
            if num_proceso and num_proceso not in ["Sin Proceso", "-", "nan", ""]:
                check_proceso = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", num_proceso).execute()
                if check_proceso.data:
                    proceso_a_guardar = f"{num_proceso} [Ref: {num_orden}]"
            
            if not proceso_a_guardar or proceso_a_guardar in ["Sin Proceso", "-", "nan", ""]:
                proceso_a_guardar = f"MANUAL-{int(time.time())}"
                
            nueva_orden = {
                "numero_proceso_sercop": proceso_a_guardar, 
                "numero_orden_compra": num_orden, 
                "razon_social_proveedor": proveedor_extraido, 
                "nombre_comercial": nombre_comercial_extraido,
                "objeto_contratacion": state_datos.get("objeto_contratacion", "Adquisición automática por Acta"), 
                "monto": float(state_datos.get("monto", 0.0)) if state_datos.get("monto") else 0.0, 
                "fecha_adquisicion": "2023-01-01"
            }
            res_insert = supabase.table("ordenes_compra").insert(nueva_orden).execute()
            orden_id = res_insert.data[0]['id']
            
        mapa_equipos_db = {str(eq["numero_serie"]).strip().upper().replace(" ", ""): eq["id"] for eq in supabase.table("equipos").select("id, numero_serie").execute().data if eq.get("numero_serie")}
        reg_nuevos, reg_actualizados = 0, 0

        for idx, row in tabla_datos.iterrows():
            tipo = str(row.iloc[0]).strip()
            marca = str(row.iloc[1]).strip()
            modelo = str(row.iloc[2]).strip()
            serie_acta = str(row.iloc[3]).strip()
            obs = str(row.iloc[8]).strip()
            
            if not serie_acta or serie_acta.lower() == 'nan': continue
            
            eq_id = mapa_equipos_db.get(serie_acta.upper().replace(" ", ""))
            if eq_id:
                supabase.table("equipos").update({
                    "tipo_equipo": tipo, 
                    "marca": marca, 
                    "modelo": modelo, 
                    "observaciones": obs
                }).eq("id", eq_id).execute()
                reg_actualizados += 1
            else:
                supabase.table("equipos").insert({
                    "orden_id": orden_id, 
                    "tipo_equipo": tipo, 
                    "marca": marca, 
                    "modelo": modelo, 
                    "numero_serie": serie_acta, 
                    "estado": "Operativo", 
                    "observaciones": obs
                }).execute()
                reg_nuevos += 1
                
        registrar_auditoria(usuario, f"Acta procesada: {reg_nuevos} nuevos, {reg_actualizados} actualizados.")
        return f"✅ {reg_nuevos} NUEVOS | 🔄 {reg_actualizados} ACTUALIZADOS.", cargar_datos_inventario(), obtener_series_disponibles()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles()

def ingresar_equipo_manual(proceso, orden, prov, nom_com, obj, monto, tipo, marca, modelo, serie, obs, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not orden or not serie: return "⚠️ Faltan datos.", cargar_datos_inventario(), obtener_series_disponibles()
    try:
        proceso_limpio = str(proceso).strip()
        resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proceso_limpio).eq("numero_orden_compra", str(orden).strip()).execute()
        if not resp_orden.data:
            resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{proceso_limpio} [Ref: {str(orden).strip()}]").eq("numero_orden_compra", str(orden).strip()).execute()
        
        orden_id = None
        
        if resp_orden.data: 
            orden_id = resp_orden.data[0]['id']
            actualizaciones = {}
            if prov: actualizaciones["razon_social_proveedor"] = prov
            if nom_com: actualizaciones["nombre_comercial"] = nom_com
            if actualizaciones:
                supabase.table("ordenes_compra").update(actualizaciones).eq("id", orden_id).execute()
        else:
            proc_g = proceso_limpio
            if proc_g and proc_g not in ["Sin Proceso", "-", "nan", ""]:
                check_proceso = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proc_g).execute()
                if check_proceso.data: proc_g = f"{proc_g} [Ref: {orden}]"
            
            if not proc_g or proc_g in ["Sin Proceso", "-", "nan", ""]:
                proc_g = f"MANUAL-{int(time.time())}"
                
            nueva_orden = {"numero_proceso_sercop": proc_g, "numero_orden_compra": orden, "razon_social_proveedor": prov or "", "nombre_comercial": nom_com or "", "objeto_contratacion": obj or "", "monto": float(monto or 0), "fecha_adquisicion": "2023-01-01"}
            orden_id = supabase.table("ordenes_compra").insert(nueva_orden).execute().data[0]['id']
            
        supabase.table("equipos").insert({"orden_id": orden_id, "tipo_equipo": tipo or "", "marca": marca or "", "modelo": modelo or "", "numero_serie": serie, "estado": "Operativo", "observaciones": obs or ""}).execute()
        registrar_auditoria(usuario, f"Ingresó equipo {serie}.")
        return f"✅ Equipo '{serie}' ingresado.", cargar_datos_inventario(), obtener_series_disponibles()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles()

def registrar_y_actualizar(cedula, nombres, cargo, departamento, request: gr.Request):
    usuario = request.username if request else "Sistema"
    cedula_limpia = cedula.strip()
    if not cedula_limpia or not nombres: return "⚠️ Faltan datos.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        res = supabase.table("funcionarios").select("id").eq("cedula", cedula_limpia).execute()
        if res.data:
            supabase.table("funcionarios").update({"nombres_completos": nombres, "cargo": cargo, "departamento": departamento}).eq("cedula", cedula_limpia).execute()
            registrar_auditoria(usuario, f"Actualizó a {nombres}.")
            mensaje = f"✅ Datos actualizados."
        else:
            supabase.table("funcionarios").insert({"cedula": cedula_limpia, "nombres_completos": nombres, "cargo": cargo, "departamento": departamento}).execute()
            registrar_auditoria(usuario, f"Registró a {nombres}.")
            mensaje = f"✅ Registrado."
        return mensaje, obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def eliminar_funcionario(cedula, request: gr.Request):
    usuario = request.username if request else "Sistema"
    cedula_limpia = cedula.strip() if cedula else ""
    if not cedula_limpia: return "⚠️ Ingresa código GAD.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        res_func = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cedula_limpia).execute()
        if not res_func.data: return "⚠️ No encontrado.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
        func_id, nombres = res_func.data[0]['id'], res_func.data[0]['nombres_completos']
        res_eq = supabase.table("equipos").select("numero_serie").eq("funcionario_id", func_id).execute()
        if res_eq.data: return f"❌ ALERTA: {nombres} tiene equipos a su cargo.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
        supabase.table("funcionarios").delete().eq("cedula", cedula_limpia).execute()
        registrar_auditoria(usuario, f"Eliminó funcionario: {nombres}.")
        return f"🗑️ Eliminado.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def advertencia_asignacion(funcionario_combo):
    if not funcionario_combo: return ""
    try:
        res = supabase.table("funcionarios").select("id").eq("cedula", funcionario_combo.split(" - ")[-1]).execute()
        if res.data:
            res_eq = supabase.table("equipos").select("tipo_equipo").eq("funcionario_id", res.data[0]['id']).execute()
            if res_eq.data: return f"⚠️ ADVERTENCIA: Ya tiene {len(res_eq.data)} equipo(s)."
        return "ℹ️ Sin equipos."
    except: return ""

def asignar_custodio_equipo(serie_combo, funcionario_combo, archivo_garantia, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_combo or not funcionario_combo: return "⚠️ Faltan datos.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    serie = serie_combo.split(" - ")[0].strip()
    cedula = funcionario_combo.split(" - ")[-1].strip()
    try:
        res_func = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cedula).execute()
        if not res_func.data: return "❌ No encontrado.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
        func_id, nombre = res_func.data[0]['id'], res_func.data[0]['nombres_completos']
        supabase.table("equipos").update({"funcionario_id": func_id}).eq("numero_serie", serie).execute()
        registrar_auditoria(usuario, f"Asignó {serie} a {nombre}.")
        msg = f"✅ Equipo {serie} asignado a {nombre}."
        if archivo_garantia:
            texto = "".join(pagina.extract_text() + "\n" for pagina in PdfReader(archivo_garantia).pages)
            res_ia = llamar_gemini_con_reintentos(f"Extrae tiempo de garantía: {texto}")
            msg += f"\n📜 GARANTÍA:\n{res_ia.text.strip()}"
        return msg, cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()

def liberar_equipo(serie_liberar_combo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_liberar_combo: return "⚠️ Selecciona un equipo.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()
    serie = serie_liberar_combo.split(" - ")[0].strip()
    try:
        supabase.table("equipos").update({"funcionario_id": None}).eq("numero_serie", serie).execute()
        registrar_auditoria(usuario, f"Liberó equipo {serie}.")
        return f"🔓 Liberado.", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(), obtener_series_disponibles(), obtener_series_asignadas()

def analizar_excel_masivo(archivo_excel):
    if not archivo_excel: return "⚠️ Sube un Excel.", []
    try:
        df_raw = pd.read_excel(archivo_excel, header=None)
        h_idx = next((i for i, r in df_raw.iterrows() if 'serie' in " ".join(str(v).lower() for v in r.values) and ('nombre' in " ".join(str(v).lower() for v in r.values) or 'custodio' in " ".join(str(v).lower() for v in r.values))), -1)
        if h_idx == -1: return "❌ No se encontró tabla válida.", []
        df = pd.read_excel(archivo_excel, header=h_idx)
        df.columns = df.columns.astype(str).str.strip().str.lower()
        
        c_serie = next((c for c in df.columns if 'serie' in c), None)
        c_ced = next((c for c in df.columns if any(x in c for x in ['cedula','cédula','identificacion','cód','cod'])), None)
        c_nom = next((c for c in df.columns if any(x in c for x in ['nombre','funcionario','custodio'])), None)
        c_car = next((c for c in df.columns if 'cargo' in c), None)
        c_dep = next((c for c in df.columns if any(x in c for x in ['departamento','area','dirección'])), None)

        equipos_db = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in supabase.table("equipos").select("numero_serie").execute().data}
        func_db = {str(f.get("nombres_completos", "")).strip().upper(): str(f.get("cedula", "")).strip() for f in supabase.table("funcionarios").select("cedula, nombres_completos").execute().data}
        
        vista_previa = []
        for _, row in df.iterrows():
            nombres = str(row[c_nom]).strip() if c_nom and pd.notna(row[c_nom]) else "Sin Nombre"
            serie = str(row[c_serie]).strip().upper().replace(" ", "") if c_serie and pd.notna(row[c_serie]) else ""
            if serie.endswith('.0'): serie = serie[:-2]
            cedula = str(row[c_ced]).strip() if c_ced and pd.notna(row[c_ced]) else ""
            if cedula.endswith('.0'): cedula = cedula[:-2]
            if not cedula or cedula.lower() == 'nan':
                for v in row.values:
                    v_str = str(v).strip().replace(".0", "")
                    if v_str.isdigit() and len(v_str) in [9, 10]: cedula = v_str; break
            if len(cedula) == 9 and cedula.isdigit(): cedula = "0" + cedula
            if (not cedula or cedula.lower() == 'nan') and nombres != "Sin Nombre": cedula = func_db.get(nombres.upper(), "")

            cargo = str(row[c_car]).strip() if c_car and pd.notna(row[c_car]) else "No definido"
            depto = str(row[c_dep]).strip() if c_dep and pd.notna(row[c_dep]) else "No definido"

            if not serie and not cedula and nombres == "Sin Nombre": continue
            
            s_mostrar = serie if serie and serie != 'NAN' else ""
            c_mostrar = cedula if cedula and cedula.lower() != 'nan' else ""
            if not s_mostrar: vista_previa.append(["", c_mostrar, nombres, cargo, depto, "❌ ERROR: Falta serie"])
            elif not equipos_db.get(s_mostrar): vista_previa.append([s_mostrar, c_mostrar, nombres, cargo, depto, "❌ ERROR: Equipo no en BD"])
            elif not c_mostrar: vista_previa.append([equipos_db.get(s_mostrar), "", nombres, cargo, depto, "❌ ERROR: Falta Código GAD"])
            else: vista_previa.append([equipos_db.get(s_mostrar), c_mostrar, nombres, cargo, depto, "✅ OK"])

        return f"✅ Análisis listo. {sum(1 for f in vista_previa if '✅' in f[-1])} válidos.", vista_previa
    except Exception as e: return f"❌ Error: {e}", []

def confirmar_asignacion_masiva(tabla_datos, request: gr.Request):
    if tabla_datos is None or tabla_datos.empty: return "⚠️ Tabla vacía."
    mapa_func = {str(f["cedula"]).strip(): f["id"] for f in supabase.table("funcionarios").select("cedula, id").execute().data}
    eq_validos = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in supabase.table("equipos").select("numero_serie").execute().data}

    exitos, nuevos, errores = 0, 0, 0
    for _, row in tabla_datos.iterrows():
        try:
            s_raw, c_raw, n_raw, car_raw, d_raw = str(row.iloc[0]).strip(), str(row.iloc[1]).strip(), str(row.iloc[2]).strip(), str(row.iloc[3]).strip(), str(row.iloc[4]).strip()
            if not s_raw or not c_raw or s_raw.lower() == 'nan': {errores: errores + 1}; continue
            s_exacta = eq_validos.get(s_raw.upper().replace(" ", ""))
            if not s_exacta: {errores: errores + 1}; continue
            
            f_id = mapa_func.get(c_raw)
            if not f_id:
                res = supabase.table("funcionarios").insert({"cedula": c_raw, "nombres_completos": n_raw, "cargo": car_raw, "departamento": d_raw}).execute()
                if res.data: f_id = res.data[0]["id"]; mapa_func[c_raw] = f_id; nuevos += 1
            if f_id:
                supabase.table("equipos").update({"funcionario_id": f_id}).eq("numero_serie", s_exacta).execute()
                exitos += 1
        except Exception: errores += 1
    registrar_auditoria(request.username if request else "Sistema", f"Carga masiva: {exitos} asignados.")
    return f"✅ Completado: {exitos} asignados, {nuevos} nuevos. (Errores: {errores})"

def cargar_tabla_edicion(termino=""):
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        datos = []
        for eq in res.data:
            ord_data = eq.get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            prov = ord_data.get("razon_social_proveedor", "") if ord_data else ""
            nomc = ord_data.get("nombre_comercial", "") if ord_data else ""

            func = eq.get("funcionarios")
            cust = func.get("nombres_completos") if func else "Sin Asignar"
            dept = func.get("departamento") if func else "-"
            
            obs = eq.get("observaciones", "") or ""

            f_str = f"{eq.get('numero_serie','')} {eq.get('tipo_equipo','')} {eq.get('marca','')} {eq.get('modelo','')} {prov} {nomc} {cust} {dept}".lower()
            if termino and termino.lower() not in f_str: continue

            datos.append([eq["id"], eq.get("numero_serie",""), eq.get("tipo_equipo",""), eq.get("marca",""), eq.get("modelo",""), cust, dept, eq.get("estado",""), prov, nomc, obs, "Mantener"])
        return datos if datos else [["-", "Sin datos", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def cargar_duplicados():
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        conteo = {}
        for eq in res.data: conteo[str(eq.get("numero_serie","")).upper().replace(" ", "")] = conteo.get(str(eq.get("numero_serie","")).upper().replace(" ", ""), 0) + 1

        datos = []
        for eq in res.data:
            if conteo[str(eq.get("numero_serie","")).upper().replace(" ", "")] > 1:
                ord_data = eq.get("ordenes_compra")
                if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
                prov = ord_data.get("razon_social_proveedor", "") if ord_data else ""
                nomc = ord_data.get("nombre_comercial", "") if ord_data else ""

                func = eq.get("funcionarios")
                cust = func.get("nombres_completos") if func else "Sin Asignar"
                dept = func.get("departamento") if func else "-"
                
                datos.append([eq["id"], eq.get("numero_serie",""), eq.get("tipo_equipo",""), eq.get("marca",""), eq.get("modelo",""), cust, dept, eq.get("estado",""), prov, nomc, eq.get("observaciones","") or "", "Mantener"])
        datos.sort(key=lambda x: str(x[1]).upper().replace(" ", ""))
        return datos if datos else [["-", "Sin duplicados", "-", "-", "-", "-", "-", "-", "-", "-", "-", "Mantener"]]
    except Exception as e: return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def confirmar_edicion_masiva(df, request: gr.Request):
    if df is None or df.empty: return "⚠️ Tabla vacía."
    exitos_edit, exitos_del, errores = 0, 0, 0

    for _, row in df.iterrows():
        try:
            id_bd_raw = str(row.iloc[0]).strip().replace(",", "")
            if id_bd_raw.endswith('.0'): id_bd_raw = id_bd_raw[:-2]
            if id_bd_raw in ["-", "nan", ""]: continue
            id_bd = id_bd_raw
            
            accion = str(row.iloc[11]).strip()
            if accion.lower() == "eliminar":
                supabase.table("equipos").delete().eq("id", id_bd).execute()
                exitos_del += 1
            else:
                serie = str(row.iloc[1]).strip()
                if serie.endswith('.0'): serie = serie[:-2]
                
                d_act = {
                    "numero_serie": serie, "tipo_equipo": str(row.iloc[2]).strip(),
                    "marca": str(row.iloc[3]).strip(), "modelo": str(row.iloc[4]).strip(),
                    "estado": str(row.iloc[7]).strip(), "observaciones": str(row.iloc[10]).strip()
                }

                res_eq = supabase.table("equipos").select("orden_id, funcionario_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").eq("id", id_bd).execute()
                if res_eq.data:
                    f_id = res_eq.data[0].get("funcionario_id")
                    if f_id: supabase.table("funcionarios").update({"nombres_completos": str(row.iloc[5]).strip(), "departamento": str(row.iloc[6]).strip()}).eq("id", f_id).execute()

                    prov_n = str(row.iloc[8]).strip()
                    nomc_n = str(row.iloc[9]).strip()
                    ord_data = res_eq.data[0].get("ordenes_compra")
                    if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
                    elif not isinstance(ord_data, dict): ord_data = {}
                    
                    if prov_n and prov_n != ord_data.get("razon_social_proveedor", "").strip():
                        res_ind = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"INDIV-{serie}").execute()
                        if res_ind.data: 
                            supabase.table("ordenes_compra").update({"razon_social_proveedor": prov_n, "nombre_comercial": nomc_n}).eq("id", res_ind.data[0]["id"]).execute()
                            d_act["orden_id"] = res_ind.data[0]["id"]
                        else: 
                            d_act["orden_id"] = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": f"INDIV-{serie}", "numero_orden_compra": "Independiente", "razon_social_proveedor": prov_n, "nombre_comercial": nomc_n, "objeto_contratacion": "Separación", "monto": 0.0, "fecha_adquisicion": "2023-01-01"}).execute().data[0]["id"]
                    else:
                        if nomc_n != ord_data.get("nombre_comercial", "").strip():
                            supabase.table("ordenes_compra").update({"nombre_comercial": nomc_n}).eq("id", res_eq.data[0].get("orden_id")).execute()

                supabase.table("equipos").update(d_act).eq("id", id_bd).execute()
                exitos_edit += 1
        except Exception as e: print(f"Error fila: {e}"); errores += 1

    registrar_auditoria(request.username if request else "Sistema", f"Edición Masiva: {exitos_edit} act, {exitos_del} elim.")
    return f"✅ {exitos_edit} actualizados, {exitos_del} eliminados. (Errores: {errores})"

def cargar_equipo_para_edicion(serie_combo):
    if not serie_combo: return "", "", "", "", ""
    serie = serie_combo.split(" - ")[0].strip()
    try:
        res = supabase.table("equipos").select("estado, tipo_equipo, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").eq("numero_serie", serie).execute()
        if res.data:
            item = res.data[0]
            estado = item.get('estado', '')
            tipo = item.get('tipo_equipo', '')
            obs = item.get('observaciones', '')
            ord_data = item.get('ordenes_compra')
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            prov = ord_data.get('razon_social_proveedor', '') if ord_data else ''
            nomc = ord_data.get('nombre_comercial', '') if ord_data else ''
            return estado, tipo, prov or '', nomc or '', obs or ''
        return "", "", "", "", ""
    except: return "", "", "", "", ""

def guardar_edicion_equipo(serie_combo, nuevo_estado, nuevo_tipo, nuevo_proveedor, nuevo_nomc, nueva_obs, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_combo: return "⚠️ Selecciona un equipo."
    serie = serie_combo.split(" - ")[0].strip()
    try:
        res_eq = supabase.table("equipos").select("orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").eq("numero_serie", serie).execute()
        if not res_eq.data: return "❌ No encontrado."

        ord_data = res_eq.data[0].get("ordenes_compra")
        if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
        elif not isinstance(ord_data, dict): ord_data = {}
        prov_actual = ord_data.get("razon_social_proveedor", "").strip()

        datos_act = {"estado": nuevo_estado, "tipo_equipo": nuevo_tipo, "observaciones": nueva_obs}
        msg = ""

        if nuevo_proveedor.strip() and nuevo_proveedor.strip() != prov_actual:
            ident = f"INDIV-{serie}"
            res_indiv = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", ident).execute()
            if res_indiv.data:
                o_id = res_indiv.data[0]["id"]
                supabase.table("ordenes_compra").update({"razon_social_proveedor": nuevo_proveedor.strip(), "nombre_comercial": nuevo_nomc.strip()}).eq("id", o_id).execute()
            else:
                o_id = supabase.table("ordenes_compra").insert({"numero_proceso_sercop": ident, "numero_orden_compra": "Independiente", "razon_social_proveedor": nuevo_proveedor.strip(), "nombre_comercial": nuevo_nomc.strip(), "objeto_contratacion": "Separación", "monto": 0.0, "fecha_adquisicion": "2023-01-01"}).execute().data[0]["id"]
            datos_act["orden_id"] = o_id
            msg = " (Proveedor independizado)."
        else:
            supabase.table("ordenes_compra").update({"nombre_comercial": nuevo_nomc.strip()}).eq("id", res_eq.data[0].get("orden_id")).execute()

        supabase.table("equipos").update(datos_act).eq("numero_serie", serie).execute()
        registrar_auditoria(usuario, f"Editó {serie}.")
        return f"✅ Guardado.{msg}"
    except Exception as e: return f"❌ Error: {e}"

def generar_reporte_personalizado(orden, custodio, columnas, formato):
    datos = cargar_datos_inventario("")
    h_c = ["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"]
    if not columnas: return gr.update(visible=False, value=None), "⚠️ Selecciona columnas."
    d_f = []
    for fila in datos:
        if fila[0] == "-": continue
        if orden and orden.lower() not in (str(fila[6]) + str(fila[7])).lower(): continue
        if custodio and custodio.lower() not in (str(fila[3]) + str(fila[4])).lower(): continue
        
        fila_adaptada = [fila[0], "-", fila[1], fila[2], fila[3], fila[4], fila[5], fila[6], fila[7], fila[8], fila[9]]
        d_f.append([fila_adaptada[h_c.index(c)] for c in columnas])
        
    if not d_f: return gr.update(visible=False, value=None), "⚠️ Sin resultados."
    
    t_d = tempfile.gettempdir()
    f_s = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        if formato == "excel":
            ruta = os.path.join(t_d, f"Reporte_{f_s}.xlsx")
            pd.DataFrame(d_f, columns=columnas).to_excel(ruta, index=False)
            return gr.update(value=ruta, visible=True), "✅ Excel generado."
        elif formato == "pdf":
            ruta = os.path.join(t_d, f"Reporte_{f_s}.pdf")
            pdf = FPDF(orientation='L', unit='mm', format='A4')
            pdf.add_page(); pdf.set_font("Arial", 'B', 14); pdf.cell(0, 12, "REPORTE INVENTARIO", ln=True, align='C')
            def l(t):
                t_s = str(t)
                for o, r in {'●':'-','✓':'OK','✗':'X'}.items(): t_s = t_s.replace(o, r)
                return t_s.encode('latin-1', 'replace').decode('latin-1')
            ps = {"Equipo": 1.5, "Marca/Modelo": 2.0, "Nº Serie": 1.5, "Custodio Asignado": 2.5, "Área/Dirección": 2.0, "Estado": 1.0, "Orden de Compra": 1.5, "Proceso SERCOP": 1.5, "Proveedor": 2.0, "Nombre Comercial": 2.0, "Observaciones": 3.0}
            pt = sum(ps[c] for c in columnas); anchos = [(ps[c]/pt)*277 for c in columnas]
            pdf.set_font("Arial", 'B', 8)
            for i, c in enumerate(columnas): pdf.cell(anchos[i], 8, l(c), 1, 0, 'C')
            pdf.ln()
            pdf.set_font("Arial", '', 7)
            for f in d_f:
                for i, it in enumerate(f):
                    txt = l(it)
                    m_c = int(anchos[i]/1.5)
                    pdf.cell(anchos[i], 6, txt[:m_c-2]+".." if len(txt)>m_c else txt, 1)
                pdf.ln()
            pdf.output(ruta)
            return gr.update(value=ruta, visible=True), "✅ PDF generado."
    except Exception as e: return gr.update(visible=False, value=None), f"❌ Error: {e}"

def exportar_pdf_inv(o, c, col): return generar_reporte_personalizado(o, c, col, "pdf")
def exportar_excel_inv(o, c, col): return generar_reporte_personalizado(o, c, col, "excel")

def gestionar_usuario_admin(usuario_mod, clave_mod, rol_mod, request: gr.Request):
    admin_req = request.username if request else "Sistema"
    if not usuario_mod: return "⚠️ Faltan datos.", cargar_usuarios_sistema(), obtener_usuarios_lista()
    try:
        res = supabase.table("usuarios_sistema").select("id").eq("usuario", usuario_mod).execute()
        if res.data:
            upd = {"rol": rol_mod}
            if clave_mod: upd["clave"] = clave_mod
            supabase.table("usuarios_sistema").update(upd).eq("usuario", usuario_mod).execute()
            registrar_auditoria(admin_req, f"Modificó usuario {usuario_mod}.")
            return "✅ Usuario actualizado.", cargar_usuarios_sistema(), obtener_usuarios_lista()
        else:
            if not clave_mod: return "⚠️ Clave requerida para nuevo usuario.", cargar_usuarios_sistema(), obtener_usuarios_lista()
            supabase.table("usuarios_sistema").insert({"usuario": usuario_mod, "clave": clave_mod, "rol": rol_mod}).execute()
            registrar_auditoria(admin_req, f"Creó usuario {usuario_mod}.")
            return "✅ Usuario creado.", cargar_usuarios_sistema(), obtener_usuarios_lista()
    except Exception as e: return f"❌ Error: {e}", cargar_usuarios_sistema(), obtener_usuarios_lista()

def generar_respaldo_manual(request: gr.Request):
    usuario = request.username if request else "Sistema"
    try:
        tablas = ["funcionarios", "ordenes_compra", "equipos", "mantenimientos", "auditoria", "usuarios_sistema"]
        respaldo = {}
        for t in tablas: respaldo[t] = supabase.table(t).select("*").execute().data
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        nombre_archivo = f"Respaldo_ERP_{timestamp}.json"
        ruta = os.path.join(tempfile.gettempdir(), nombre_archivo)
        
        with open(ruta, "w", encoding="utf-8") as f: json.dump(respaldo, f, ensure_ascii=False, indent=4)
        registrar_auditoria(usuario, "Generó respaldo manual (JSON).")
        return gr.update(value=ruta, visible=True), "✅ Respaldo generado con éxito."
    except Exception as e: return gr.update(visible=False, value=None), f"❌ Error: {e}"

def restaurar_base_datos(archivo_json, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not archivo_json: return "⚠️ Sube un archivo JSON de respaldo válido."
    
    try:
        with open(archivo_json, "r", encoding="utf-8") as f:
            datos_respaldo = json.load(f)
            
        tablas_orden = ["usuarios_sistema", "funcionarios", "ordenes_compra", "equipos", "mantenimientos", "auditoria"]
        total_registros = 0
        
        for tabla in tablas_orden:
            if tabla in datos_respaldo:
                registros = datos_respaldo[tabla]
                if not registros: continue
                
                bloque_size = 500
                for i in range(0, len(registros), bloque_size):
                    bloque = registros[i:i + bloque_size]
                    supabase.table(tabla).upsert(bloque).execute()
                    total_registros += len(bloque)
                    
        registrar_auditoria(usuario, f"Restauró la base de datos completa ({total_registros} registros).")
        return f"✅ Sistema restaurado con éxito. Se procesaron {total_registros} registros. La página se actualizará."
    except Exception as e:
        return f"❌ Error al restaurar: {e}"

def cargar_todo_ui():
    return (
        cargar_datos_inventario(), 
        obtener_series_disponibles(), 
        obtener_funcionarios(),
        obtener_series_asignadas(), 
        obtener_todas_las_series(),
        cargar_listado_funcionarios(),
        obtener_funcionarios(), 
        obtener_usuarios_lista(), 
        obtener_funcionarios_mantenimiento()
    )

def inicializar_sistema_completo(request: gr.Request):
    es_admin = False
    try:
        res = supabase.table("usuarios_sistema").select("rol").eq("usuario", request.username if request else "").execute()
        if res.data and res.data[0].get('rol') == 'admin': es_admin = True
    except: pass
    return (
        cargar_datos_auditoria(), cargar_usuarios_sistema(), cargar_datos_inventario(), 
        obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas(), 
        obtener_todas_las_series(), cargar_listado_funcionarios(), obtener_funcionarios(), 
        obtener_usuarios_lista(), obtener_funcionarios_mantenimiento(), 
        gr.update(visible=es_admin), gr.update(visible=not es_admin)
    )

def seleccionar_funcionario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        ced, nom = str(tabla_actual.iloc[evt.index[0], 0]), str(tabla_actual.iloc[evt.index[0], 1])
        return ced, nom, str(tabla_actual.iloc[evt.index[0], 2]), str(tabla_actual.iloc[evt.index[0], 3]), f"{nom} - {ced}"
    except: return "", "", "", "", None

def seleccionar_inventario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        serie = str(tabla_actual.iloc[evt.index[0], 2])
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").eq("numero_serie", serie).execute()
        if res.data:
            s_full = f"{serie} - {res.data[0].get('tipo_equipo','')} {res.data[0].get('marca','')}".strip()
            return s_full, s_full
        return None, None
    except: return None, None

def seleccionar_usuario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        usr, rol = str(tabla_actual.iloc[evt.index[0], 0]), str(tabla_actual.iloc[evt.index[0], 1]).lower()
        return usr, rol if rol in ["admin", "operador"] else "admin", f"{usr} - {rol}"
    except: return "", "admin", None

def cargar_datos_funcionario_form(combo):
    if not combo: return "", "", "", ""
    res = supabase.table("funcionarios").select("*").eq("cedula", str(combo).split(" - ")[-1].strip()).execute()
    if res.data: f = res.data[0]; return f.get("cedula",""), f.get("nombres_completos",""), f.get("cargo",""), f.get("departamento","")
    return "", "", "", ""

def cargar_datos_usuario_form(combo):
    if not combo: return "", "admin"
    res = supabase.table("usuarios_sistema").select("*").eq("usuario", str(combo).split(" - ")[0].strip()).execute()
    if res.data: return res.data[0].get("usuario",""), res.data[0].get("rol","admin")
    return "", "admin"


# ACTUALIZADO: Quitamos `theme=` de gr.Blocks() como manda Gradio 6.0
with gr.Blocks() as erp_interfaz:
    
    with gr.Row():
        gr.Markdown("<h1 style='text-align: left; width: 80%; color: #2c3e50; margin-left: 10px;'>🏛️ Sistema Integrado de Contratación y Vigencia Tecnológica</h1>")
        btn_logout = gr.Button("🚪 Cerrar Sesión", variant="stop", scale=1)
    
    with gr.Tabs():
        
        # PESTAÑA 1
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
            btn_analizar.click(fn=analizar_fase_precontractual, inputs=[archivos_input, referencias_input, enlaces_input], outputs=reporte_output)

        # PESTAÑA 2
        with gr.TabItem("2. Custodios e Inventario"):
            with gr.Row():
                with gr.Column(scale=1):
                    with gr.Group():
                        gr.Markdown("#### 📥 Ingreso de Bienes")
                        with gr.Accordion("1. Ingreso Automático (Acta PDF)", open=False):
                            acta_input = gr.File(label="Subir Acta (PDF)", type="filepath")
                            btn_analizar_acta = gr.Button("1. Extraer y Previsualizar Datos", variant="secondary")
                            
                            tabla_preview_acta = gr.Dataframe(
                                headers=["Tipo", "Marca", "Modelo", "Serie", "Proveedor", "Nombre Comercial", "Orden de Compra", "Proceso SERCOP", "Observaciones"],
                                interactive=True, wrap=True,
                                label="Vista Previa (Puedes editar antes de guardar)"
                            )
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
                        gr.Markdown("#### 👤 Gestión de Personal (Funcionarios)")
                        with gr.Accordion("3. Listado e Ingreso de Personal", open=False):
                            buscar_func_combo = gr.Dropdown(label="Buscar Funcionario Registrado", choices=[], interactive=True)
                            cedula_in = gr.Textbox(label="Código del GAD (Identificador Único)")
                            nombres_in = gr.Textbox(label="Nombres Completos")
                            cargo_in = gr.Textbox(label="Cargo")
                            depto_in = gr.Textbox(label="Departamento")
                            with gr.Row():
                                btn_guardar_func = gr.Button("💾 Guardar", variant="primary")
                                btn_limpiar_func = gr.Button("🧹 Limpiar", variant="secondary")
                                btn_eliminar_func = gr.Button("🗑️ Eliminar", variant="stop")
                            mensaje_func = gr.Textbox(show_label=False, interactive=False, lines=4)
                            tabla_funcionarios = gr.Dataframe(headers=["Código GAD", "Nombre", "Cargo", "Departamento"], interactive=False, wrap=True)
                    
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
                            tabla_preview = gr.Dataframe(headers=["Nº Serie", "Código GAD", "Nombres", "Cargo", "Departamento", "Validación"], interactive=True, wrap=True)
                            btn_confirmar_masivo = gr.Button("2. Confirmar Asignación", variant="primary")
                            mensaje_masivo = gr.Textbox(label="Resultado", interactive=False)

                    with gr.Group():
                        gr.Markdown("#### ⚙️ Mantenimiento del Inventario")
                        with gr.Accordion("6. Edición Masiva y Limpieza", open=False):
                            with gr.Row():
                                serie_liberar = gr.Dropdown(label="Equipo Ocupado (Liberar)", choices=[], interactive=True)
                                btn_liberar = gr.Button("🔓 Liberar Equipo", variant="stop")
                            mensaje_liberar = gr.Textbox(show_label=False, interactive=False)
                            
                            with gr.Row():
                                txt_buscar_edicion = gr.Textbox(label="Buscar equipo para editar tabla", placeholder="Ej: Serie")
                                btn_buscar_edicion = gr.Button("🔍 Buscar", variant="secondary")
                                btn_buscar_duplicados = gr.Button("⚠️ Duplicados", variant="stop")
                                
                            tabla_edicion = gr.Dataframe(
                                headers=["ID BD", "Nº Serie", "Tipo", "Marca", "Modelo", "Custodio Asignado", "Área/Dirección", "Estado", "Proveedor", "Nombre Comercial", "Observaciones", "Acción"],
                                interactive=True, wrap=True
                            )
                            btn_guardar_edicion_tabla = gr.Button("💾 Confirmar Cambios Tabla", variant="primary")
                            mensaje_edicion_tabla = gr.Textbox(label="Estado Edición", interactive=False)
                            
                            serie_editar = gr.Dropdown(label="Editar por Formulario (Cualquier Equipo)", choices=[])
                            estado_editar = gr.Dropdown(label="Estado", choices=["Operativo", "En Mantenimiento", "De Baja", "Dañado"])
                            tipo_editar = gr.Textbox(label="Tipo de Equipo")
                            proveedor_editar = gr.Textbox(label="Proveedor")
                            nombre_comercial_editar = gr.Textbox(label="Nombre Comercial")
                            observaciones_editar = gr.Textbox(label="Observaciones")
                            btn_guardar_edicion = gr.Button("💾 Guardar Formulario", variant="primary")
                            mensaje_edicion = gr.Textbox(show_label=False, interactive=False)

                with gr.Column(scale=2):
                    with gr.Group():
                        gr.Markdown("### 🔍 Buscador Universal y Visor de Inventario")
                        with gr.Row():
                            caja_busqueda = gr.Textbox(label="Buscar (Serie, Cédula, Orden...)", scale=4)
                            btn_buscar = gr.Button("🔎 Buscar", variant="primary", scale=1)
                            btn_sincronizar = gr.Button("🔄 Mostrar Todos", scale=1)
                        
                        tabla_inventario = gr.Dataframe(
                            headers=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"], 
                            interactive=False, wrap=True
                        )

                    with gr.Group():
                        with gr.Accordion("🖨️ Generar Reporte Personalizado", open=False):
                            with gr.Row():
                                rep_orden = gr.Textbox(label="Filtrar por Orden/Proceso")
                                rep_custodio = gr.Textbox(label="Filtrar por Custodio/Área")
                            
                            rep_columnas = gr.CheckboxGroup(
                                choices=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"],
                                value=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Nombre Comercial"],
                                label="Columnas a Imprimir"
                            )
                            with gr.Row():
                                btn_rep_pdf = gr.Button("📄 Descargar PDF", variant="primary")
                                btn_rep_excel = gr.Button("📊 Descargar Excel", variant="secondary")
                                
                            rep_mensaje = gr.Textbox(show_label=False, interactive=False)
                            rep_archivo = gr.File(label="Archivo Reporte", visible=False)
                            
        # PESTAÑA 3
        with gr.TabItem("3. Gestión de Mantenimientos"):
            gr.Markdown("### 🛠️ Registro Técnico y Generación de Reportes")
            with gr.Row():
                with gr.Column(scale=1):
                    custodio_mantenimiento = gr.Dropdown(label="1. Funcionario / Ubicación", choices=[], interactive=True)
                    serie_mantenimiento = gr.Dropdown(label="2. Equipo(s) a intervenir", choices=[], interactive=True, multiselect=True)
                    
                    with gr.Row():
                        fecha_mantenimiento = gr.Textbox(label="3. Fecha (YYYY-MM-DD)", elem_classes="fecha-calendario", lines=1, max_lines=1)
                        proximo_mantenimiento = gr.Textbox(label="4. Próximo Mantenimiento (YYYY-MM-DD)", elem_classes="fecha-calendario", lines=1, max_lines=1)

                    tipo_mantenimiento = gr.Dropdown(label="5. Tipo de Intervención", choices=["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"])
                    tecnico_proveedor = gr.Dropdown(label="6. Empresa / Técnico Asignado", choices=[], interactive=True, allow_custom_value=True)

                    desc_checks = gr.Dropdown(
                        label="7. Trabajos Realizados", 
                        choices=["Mantenimiento Lógico", "Mantenimiento Físico"], 
                        multiselect=True, allow_custom_value=True,
                        info="Selecciona el tipo. El sistema agregará el detalle completo automáticamente."
                    )

                    with gr.Accordion("⚙️ Personalizar textos automáticos (Opcional)", open=False):
                        gr.Markdown("Si deseas cambiar lo que dice el reporte al elegir 'Mantenimiento Físico' o 'Lógico', edita estos textos antes de guardar:")
                        plantilla_logico = gr.Textbox(label="Plantilla: Mantenimiento Lógico", value="  - Verificación de funcionamiento del equipo previo a la intervención.\n  - Borrado de archivos temporales, limpieza de entradas de registro innecesarias y desinstalación de programas no requeridos para mejorar el rendimiento.\n  - Revisión detallada de logs del sistema operativo para identificar alertas de hardware.", lines=4)
                        plantilla_fisico = gr.Textbox(label="Plantilla: Mantenimiento Físico", value="  - CPU y Periféricos: Limpieza profunda física, interna y externa del CPU, Monitor LED 21.5\", teclado y mouse; comprobación y ajuste de botones, puertos funcionales y conectores.\n  - Tarjeta Principal (Mainboard): Remoción completa de polvo y residuos en contactos de circuitería, slots de expansión de la placa base (Chipset Q670) y módulos de memoria RAM; ajuste manual de cables de datos y buses de alimentación eléctrica.\n  - Fuente de Poder: Desarmado y limpieza profunda del ventilador (FAN) interno de la fuente, y verificación de los niveles de voltaje para garantizar el suministro eléctrico estable de los componentes.", lines=5)
                    
                    desc_mantenimiento = gr.Textbox(label="Detalles adicionales / Observaciones", lines=2)
                    costo_mantenimiento = gr.Number(label="8. Costo Asociado ($)", value=0.0)

                    # ACUMULADOR DE FOTOS
                    gr.Markdown("#### 📸 9. Registro Fotográfico")
                    with gr.Row():
                        foto_camara = gr.Image(label="Tomar/Subir Foto", sources=["webcam", "upload"], type="filepath", scale=2)
                        btn_agregar_foto = gr.Button("➕ Agregar esta foto y tomar otra", variant="secondary", scale=1)
                    
                    estado_fotos_camara = gr.State([])
                    galeria_fotos = gr.Gallery(label="Fotos acumuladas para el reporte", show_label=True, columns=3, height=200)
                    btn_limpiar_galeria = gr.Button("🗑️ Limpiar fotos acumuladas")
                    fotos_mantenimiento = gr.File(label="Subir fotos masivas desde archivo (Opcional)", file_count="multiple", file_types=["image"], type="filepath")

                    gr.Markdown("#### ✍️ Firmas de Conformidad")
                    nombre_admin_firma = gr.Textbox(label="Nombre: Administrador de la Orden")
                    nombre_funcionario_firma = gr.Textbox(label="Nombre: Funcionario a Cargo")
                    nombre_tecnico_firma = gr.Textbox(label="Nombre: Técnico del Proveedor")

                    btn_guardar_mantenimiento = gr.Button("💾 Registrar y Generar Reporte", variant="primary")
                    msg_mantenimiento = gr.Textbox(show_label=False, interactive=False)
                    archivo_acta_descarga = gr.File(label="📄 Reporte Generado", visible=False)

                    gr.Markdown("---")
                    gr.Markdown("#### 🗑️ Gestionar Registros")
                    registro_eliminar = gr.Dropdown(label="Selecciona Registro", choices=[], interactive=True)
                    with gr.Row():
                        btn_descargar_reporte = gr.Button("📥 Descargar PDF", variant="secondary")
                        btn_eliminar_mant = gr.Button("🗑️ Eliminar Registro", variant="stop")
                    archivo_reporte_descarga = gr.File(label="📄 Reporte Descargado", visible=False)
                    msg_eliminar = gr.Textbox(show_label=False, interactive=False)
                    
                    with gr.Accordion("✏️ Editar Registro Seleccionado (Planificación)", open=False):
                        gr.Markdown("*(1. Selecciona Funcionario arriba -> 2. Selecciona Registro -> 3. Edita aquí)*")
                        edit_fecha = gr.Textbox(label="Fecha Realizada (YYYY-MM-DD)", elem_classes="fecha-calendario")
                        edit_proximo = gr.Textbox(label="Próximo Mantenimiento (YYYY-MM-DD)", elem_classes="fecha-calendario")
                        edit_tipo = gr.Dropdown(label="Tipo", choices=["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"])
                        edit_tecnico = gr.Textbox(label="Técnico/Empresa")
                        edit_desc = gr.Textbox(label="Descripción / Trabajos", lines=4)
                        edit_costo = gr.Number(label="Costo ($)")
                        btn_guardar_edicion_mant = gr.Button("💾 Guardar Edición (Actualiza BD)", variant="primary")
                        msg_edicion_mant = gr.Textbox(show_label=False, interactive=False)
                        
                        gr.Markdown("---")
                        gr.Markdown("#### 📜 PDF Original como Referencia")
                        visor_pdf_edicion = gr.File(label="Reporte Fotográfico Existente", visible=False, interactive=False)

                with gr.Column(scale=2):
                    gr.Markdown("#### 📋 Historial Técnico")
                    tabla_mantenimientos = gr.Dataframe(
                        headers=["Fecha", "Tipo", "Responsable", "Equipo(s) Intervenido(s)", "Descripción", "Costo", "Próxima Revisión"],
                        interactive=False, wrap=True
                    )

        # PESTAÑA 4
        with gr.TabItem("4. Auditoría de Sistema"):
            gr.Markdown("### 🕵️‍♂️ Bitácora Histórica (Inalterable)")
            btn_refrescar_auditoria = gr.Button("🔄 Refrescar Registro")
            tabla_auditoria = gr.Dataframe(headers=["Fecha y Hora", "Usuario", "Acción Ejecutada"], interactive=False, wrap=True)
            btn_refrescar_auditoria.click(fn=cargar_datos_auditoria, inputs=[], outputs=tabla_auditoria)

        # PESTAÑA 5
        with gr.TabItem("5. Centro de Seguridad y Accesos"):
            panel_denegado = gr.Group(visible=False)
            with panel_denegado:
                gr.Markdown("### 🚫 Acceso Denegado\nNo tienes privilegios de Administrador.")

            panel_seguridad = gr.Group(visible=False)
            with panel_seguridad:
                gr.Markdown("### 🔐 Gestión de Administradores")
                with gr.Row():
                    with gr.Column():
                        btn_refrescar_usuarios = gr.Button("🔄 Cargar Usuarios")
                        tabla_usuarios = gr.Dataframe(headers=["Usuario", "Rol"], interactive=False, wrap=True)
                    
                    with gr.Column():
                        buscar_usuario_combo = gr.Dropdown(label="Buscar Usuario", choices=[], interactive=True)
                        usuario_db_in = gr.Textbox(label="Usuario a Editar / Crear")
                        rol_db_in = gr.Dropdown(label="Rol Asignado", choices=["admin", "operador"], value="admin")
                        clave_db_in = gr.Textbox(label="Contraseña (Nueva/Actualizar)", type="password")
                        with gr.Row():
                            btn_reset_clave = gr.Button("💾 Guardar", variant="primary")
                            btn_limpiar_usr = gr.Button("🧹 Limpiar", variant="secondary")
                        mensaje_clave = gr.Textbox(show_label=False, interactive=False)
                
                gr.Markdown("---")
                gr.Markdown("### 💾 Respaldo y Restauración de Base de Datos")
                with gr.Row():
                    btn_respaldo = gr.Button("⬇️ Generar y Descargar Respaldo Manual", variant="primary")
                with gr.Row():
                    msg_respaldo = gr.Textbox(show_label=False, interactive=False)
                    archivo_respaldo = gr.File(label="Archivo de Respaldo Generado (JSON)", visible=False)
                
                gr.Markdown("---")
                gr.Markdown("### ♻️ Restaurar Sistema")
                with gr.Row():
                    archivo_subir_respaldo = gr.File(label="Sube tu archivo de respaldo (.json)", type="filepath")
                    btn_restaurar = gr.Button("⚠️ Restaurar Base de Datos", variant="stop")
                msg_restaurar = gr.Textbox(show_label=False, interactive=False)


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

    tabla_inventario.select(fn=seleccionar_inventario_tabla, inputs=[tabla_inventario], outputs=[serie_editar, serie_liberar])
    btn_buscar.click(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    caja_busqueda.submit(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    btn_liberar.click(fn=liberar_equipo, inputs=[serie_liberar], outputs=[mensaje_liberar, tabla_inventario, serie_asignar, serie_liberar])
    
    btn_buscar_edicion.click(fn=cargar_tabla_edicion, inputs=[txt_buscar_edicion], outputs=[tabla_edicion])
    btn_buscar_duplicados.click(fn=cargar_duplicados, inputs=[], outputs=[tabla_edicion])
    btn_guardar_edicion_tabla.click(fn=confirmar_edicion_masiva, inputs=[tabla_edicion], outputs=[mensaje_edicion_tabla]).then(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    
    serie_editar.change(fn=cargar_equipo_para_edicion, inputs=[serie_editar], outputs=[estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar])
    btn_guardar_edicion.click(fn=guardar_edicion_equipo, inputs=[serie_editar, estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar], outputs=[mensaje_edicion]).then(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])

    btn_rep_pdf.click(fn=exportar_pdf_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])
    btn_rep_excel.click(fn=exportar_excel_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])

    btn_confirmar_masivo.click(fn=confirmar_asignacion_masiva, inputs=[tabla_preview], outputs=[mensaje_masivo]).then(
        fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento]
    )
    
    btn_sincronizar.click(fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento])


    custodio_mantenimiento.change(
        fn=cargar_equipos_de_funcionario, inputs=[custodio_mantenimiento], outputs=[serie_mantenimiento, nombre_funcionario_firma]
    ).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos]
    ).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar])

    serie_mantenimiento.change(fn=auto_completar_mantenimiento, inputs=[serie_mantenimiento], outputs=[tecnico_proveedor, tabla_mantenimientos])

    btn_agregar_foto.click(fn=acumular_foto_camara, inputs=[foto_camara, estado_fotos_camara], outputs=[estado_fotos_camara, galeria_fotos, foto_camara])
    btn_limpiar_galeria.click(fn=limpiar_galeria, inputs=[], outputs=[estado_fotos_camara, galeria_fotos, foto_camara])

    btn_guardar_mantenimiento.click(
        fn=registrar_y_generar_acta,
        inputs=[custodio_mantenimiento, serie_mantenimiento, fecha_mantenimiento, tipo_mantenimiento,
                desc_checks, desc_mantenimiento, tecnico_proveedor, costo_mantenimiento, proximo_mantenimiento, 
                fotos_mantenimiento, estado_fotos_camara, nombre_admin_firma, nombre_tecnico_firma, nombre_funcionario_firma,
                plantilla_logico, plantilla_fisico],
        outputs=[msg_mantenimiento, tabla_mantenimientos, archivo_acta_descarga]
    ).then(fn=limpiar_galeria, inputs=[], outputs=[estado_fotos_camara, galeria_fotos, foto_camara]
    ).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar])

    btn_descargar_reporte.click(fn=descargar_reporte_mantenimiento, inputs=[registro_eliminar], outputs=[archivo_reporte_descarga, msg_eliminar])
    
    btn_eliminar_mant.click(
        fn=eliminar_mantenimiento, inputs=[registro_eliminar], outputs=[msg_eliminar]
    ).then(fn=obtener_mantenimientos_lista_por_funcionario, inputs=[custodio_mantenimiento], outputs=[registro_eliminar]
    ).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos])

    registro_eliminar.change(
        fn=cargar_datos_mantenimiento_edicion,
        inputs=[registro_eliminar],
        outputs=[edit_fecha, edit_proximo, edit_tipo, edit_tecnico, edit_desc, edit_costo, visor_pdf_edicion]
    )

    btn_guardar_edicion_mant.click(
        fn=guardar_edicion_mantenimiento,
        inputs=[registro_eliminar, edit_fecha, edit_proximo, edit_tipo, edit_tecnico, edit_desc, edit_costo],
        outputs=[msg_edicion_mant]
    ).then(fn=cargar_historial_por_funcionario, inputs=[custodio_mantenimiento], outputs=[tabla_mantenimientos])


    btn_respaldo.click(fn=generar_respaldo_manual, inputs=[], outputs=[archivo_respaldo, msg_respaldo])
    tabla_usuarios.select(fn=seleccionar_usuario_tabla, inputs=[tabla_usuarios], outputs=[usuario_db_in, rol_db_in, buscar_usuario_combo])
    buscar_usuario_combo.change(fn=cargar_datos_usuario_form, inputs=[buscar_usuario_combo], outputs=[usuario_db_in, rol_db_in])
    btn_limpiar_usr.click(fn=lambda: ("", "admin", "", gr.update(value=None)), inputs=[], outputs=[usuario_db_in, rol_db_in, clave_db_in, buscar_usuario_combo])
    btn_refrescar_usuarios.click(fn=cargar_usuarios_sistema, inputs=[], outputs=[tabla_usuarios])
    btn_reset_clave.click(fn=gestionar_usuario_admin, inputs=[usuario_db_in, clave_db_in, rol_db_in], outputs=[mensaje_clave, tabla_usuarios, buscar_usuario_combo])
    btn_restaurar.click(fn=restaurar_base_datos, inputs=[archivo_subir_respaldo], outputs=[msg_restaurar]).then(
        fn=cargar_todo_ui, inputs=[], outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento]
    )

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
    )

import fastapi
app = fastapi.FastAPI()
app = gr.mount_gradio_app(app, erp_interfaz, path="/")

if __name__ == "__main__":
    # ACTUALIZADO: El tema gráfico se pasa aquí a `launch()` y usamos `server_port` para la versión 6.0+
    erp_interfaz.launch(server_name="0.0.0.0", server_port=8050, auth=verificar_credenciales, theme=gr.themes.Soft())
