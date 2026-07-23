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

# --- CONFIGURACIÓN DE SUPABASE Y GEMINI PARA LA NUBE ---
from supabase import create_client, Client, ClientOptions
from google import genai

load_dotenv()

# El .strip() asegura que el servidor ignore espacios en blanco accidentales en tus claves
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

# --- 1.1 MOTOR DE IA CON REINTENTOS AUTOMÁTICOS (SOLUCIÓN ERROR 503) ---
def llamar_gemini_con_reintentos(prompt, max_reintentos=4):
    """Maneja errores 503 (Servidor saturado) o 429 reintentando automáticamente."""
    for intento in range(max_reintentos):
        try:
            return gemini_client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        except Exception as e:
            error_str = str(e)
            if '503' in error_str or '429' in error_str or 'UNAVAILABLE' in error_str:
                if intento < max_reintentos - 1:
                    tiempo_espera = 2 ** intento  # Espera 1s, 2s, 4s...
                    print(f"⚠️ Servidor IA saturado (503). Reintentando en {tiempo_espera}s... (Intento {intento + 1}/{max_reintentos})")
                    time.sleep(tiempo_espera)
                else:
                    raise Exception("Los servidores de IA de Google están experimentando demasiada demanda. Por favor, intenta de nuevo en un par de minutos.")
            else:
                raise e

# --- MOTOR RESPALDO AUTOMÁTICO ---
def obtener_todos_los_registros(tabla):
    """Extrae todos los registros de una tabla en Supabase manejando la paginación."""
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
    """Descarga toda la BD y la guarda en un archivo JSON."""
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
        print(f"✅ Respaldo automático creado: {ruta_archivo}")
        return ruta_archivo
    except Exception as e:
        print(f"❌ Error en respaldo: {e}")
        return None

def generar_respaldo_manual(request: gr.Request):
    usuario = request.username if request else "Sistema"
    ruta = respaldar_base_datos()
    if ruta:
        registrar_auditoria(usuario, "Generó descarga manual del respaldo de la base de datos.")
        return gr.update(value=ruta, visible=True), "✅ Respaldo generado y listo para descargar."
    return gr.update(visible=False, value=None), "❌ Error al generar el respaldo."

# --- 2. SEGURIDAD Y AUDITORÍA ---
def verificar_credenciales(usuario, clave):
    try:
        res = supabase.table("usuarios_sistema").select("*").eq("usuario", usuario).eq("clave", clave).execute()
        return len(res.data) > 0
    except Exception: return False

def registrar_auditoria(usuario, accion):
    try: supabase.table("auditoria_sistema").insert({"usuario": usuario, "accion": accion}).execute()
    except Exception as e: print(f"Fallo al registrar auditoría: {e}")

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

# --- 3. INVENTARIO Y PERSONAL ---
def analizar_fase_precontractual(archivos_principales, archivos_referencia, fuentes_texto, request: gr.Request):
    usuario = request.username if request else "Sistema"
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
        prompt = f"Audita la fase precontractual (LOSNCP).\nCONTEXTO: {texto_contexto}\nENLACES: {fuentes_texto}"
        respuesta = llamar_gemini_con_reintentos(prompt)
        registrar_auditoria(usuario, "Ejecutó un análisis precontractual.")
        return respuesta.text
    except Exception as e: return f"❌ Error de IA: {e}"

def cargar_datos_inventario(termino_busqueda="", as_styled=True):
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
            
            if func:
                funcionario = func.get("nombres_completos") or "Sin Asignar"
                departamento = func.get("departamento") or "-"
            else:
                funcionario = "Sin Asignar"
                departamento = "-"
                
            ord_data = item.get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            elif not isinstance(ord_data, dict): ord_data = {}
            
            if ord_data:
                num_ce = ord_data.get("numero_orden_compra", "Sin CE")
                num_proceso = ord_data.get("numero_proceso_sercop", "Sin Proceso")
                proveedor = ord_data.get("razon_social_proveedor", "No registrado")
                nombre_comercial = ord_data.get("nombre_comercial", "") or ""
            else:
                num_ce = "Sin Orden"
                num_proceso = "-"
                proveedor = "-"
                nombre_comercial = "-"
            
            fila = [equipo, marca_modelo, serie, funcionario, departamento, estado, num_ce, num_proceso, proveedor, nombre_comercial, observaciones]
            if termino_busqueda:
                termino = termino_busqueda.lower()
                fila_str = " ".join([str(x) for x in fila]).lower()
                if termino not in fila_str:
                    continue
            datos.append(fila)
            
        if not datos:
            datos = [["-", "-", "Sin resultados", "-", "-", "-", "-", "-", "-", "-", "-"]]
            
        if not as_styled:
            return datos
            
        columnas = ["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"]
        df = pd.DataFrame(datos, columns=columnas)
        
        def colorear_filas(row):
            if row["Estado"] in ["De Baja", "Dañado", "Eliminado"]:
                return ['background-color: #ffcdd2; color: #b71c1c'] * len(row)
            elif row["Custodio Asignado"] in ["Sin Asignar", "Sin Asignar - BODEGA"] and row["Equipo"] != "-":
                return ['background-color: #ffe0b2; color: #e65100'] * len(row)
            return [''] * len(row)
            
        return df.style.apply(colorear_filas, axis=1)
        
    except Exception as e:
        datos_error = [["Error", "de", "conexión", "-", "-", "-", "-", "-", "-", "-", str(e)]]
        if not as_styled: return datos_error
        df_error = pd.DataFrame(datos_error, columns=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"])
        return df_error

def cargar_listado_funcionarios():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos, cargo, departamento").order("nombres_completos").execute()
        datos = [[item.get("cedula", ""), item.get("nombres_completos", ""), item.get("cargo", ""), item.get("departamento", "")] for item in res.data]
        return gr.update(value=datos if datos else [["-", "Sin registros", "-", "-"]])
    except Exception as e: return gr.update(value=[["Error", str(e), "-", "-"]])

def obtener_series_disponibles():
    try:
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, marca, funcionario_id").execute()
        series = [f"{item['numero_serie']} - {item.get('tipo_equipo', '')} {item.get('marca', '')}" for item in res.data if item.get("funcionario_id") is None]
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
        res = supabase.table("equipos").select("numero_serie, tipo_equipo, funcionario_id, funcionarios(nombres_completos)").execute()
        series = []
        for item in res.data:
            if item.get("funcionario_id") is not None:
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
    return cargar_datos_inventario(termino, as_styled=True)

# --- 3.1 FUNCIONES DE MANTENIMIENTO PROTEGIDAS ---
def obtener_funcionarios_mantenimiento():
    try:
        res = supabase.table("funcionarios").select("cedula, nombres_completos").execute()
        lista = [f"{item['nombres_completos']} - {item['cedula']}" for item in res.data]
        lista.insert(0, "Sin Asignar - BODEGA")
        return gr.update(choices=lista, value=None)
    except Exception: return gr.update(choices=[], value=None)

def cargar_equipos_de_funcionario(funcionario_combo):
    try:
        if not funcionario_combo:
            return gr.update(choices=[], value=[]), ""
        
        combo_str = str(funcionario_combo).strip()
        if combo_str == "Sin Asignar - BODEGA":
            res_eq = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").is_("funcionario_id", "null").execute()
            equipos = [f"{eq['numero_serie']} - {eq.get('tipo_equipo','')} {eq.get('marca','')}" for eq in res_eq.data]
            return gr.update(choices=equipos, value=[]), "Administrador de Bodega"

        partes = combo_str.split(" - ")
        cedula = partes[-1].strip() if len(partes) > 1 else combo_str
        
        res_func = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cedula).execute()
        if not res_func.data:
            return gr.update(choices=[], value=[]), ""
        
        func_id = res_func.data[0]['id']
        nombre_func = res_func.data[0]['nombres_completos']

        res_eq = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").eq("funcionario_id", func_id).execute()
        equipos = [f"{eq['numero_serie']} - {eq.get('tipo_equipo','')} {eq.get('marca','')}" for eq in res_eq.data]

        return gr.update(choices=equipos, value=[]), nombre_func
    except Exception as e:
        print(f"Error en cargar_equipos_de_funcionario: {e}")
        return gr.update(choices=[], value=[]), ""

def obtener_series_de_funcionario(funcionario_combo):
    try:
        if not funcionario_combo:
            return []
        combo_str = str(funcionario_combo).strip()
        if combo_str == "Sin Asignar - BODEGA":
            res_eq = supabase.table("equipos").select("numero_serie").is_("funcionario_id", "null").execute()
            return [eq.get("numero_serie") for eq in res_eq.data if eq.get("numero_serie")]
            
        partes = combo_str.split(" - ")
        cedula = partes[-1].strip() if len(partes) > 1 else combo_str
        
        res_func = supabase.table("funcionarios").select("id").eq("cedula", cedula).execute()
        if not res_func.data:
            return []
        func_id = res_func.data[0]['id']
        res_eq = supabase.table("equipos").select("numero_serie").eq("funcionario_id", func_id).execute()
        return [eq.get("numero_serie") for eq in res_eq.data if eq.get("numero_serie")]
    except Exception:
        return []

def cargar_historial_mantenimientos(serie_combo):
    try:
        if not serie_combo: return [["-", "-", "-", "-", "-", "-", "-"]]

        if isinstance(serie_combo, list):
            if not serie_combo:
                return [["-", "-", "-", "-", "-", "-", "-"]]
            series_limpias = [str(s).split(" - ")[0].strip() for s in serie_combo]
        else:
            series_limpias = [str(serie_combo).split(" - ")[0].strip()]

        return _historial_por_series(series_limpias)
    except Exception:
        return [["-", "-", "-", "-", "-", "-", "-"]]

def cargar_historial_por_funcionario(funcionario_combo):
    try:
        if not funcionario_combo:
            return [["-", "-", "-", "-", "-", "-", "-"]]

        series_limpias = obtener_series_de_funcionario(funcionario_combo)
        if not series_limpias:
            return [["-", "Este funcionario no tiene equipos asignados", "-", "-", "-", "-", "-"]]

        return _historial_por_series(series_limpias)
    except Exception:
        return [["-", "-", "-", "-", "-", "-", "-"]]

def _historial_por_series(series_limpias):
    try:
        res = supabase.table("mantenimientos").select("*").in_("numero_serie", series_limpias).order("id", desc=True).execute()
        datos = []
        for item in res.data:
            numero_informe = item.get("numero_informe")
            equipos_str = item.get("numero_serie", "")

            if numero_informe:
                try:
                    res_grupo = supabase.table("mantenimientos").select("numero_serie").eq("numero_informe", numero_informe).execute()
                    series_grupo = [g.get("numero_serie", "") for g in res_grupo.data if g.get("numero_serie")]
                    if series_grupo:
                        equipos_str = ", ".join(sorted(set(series_grupo)))
                except Exception:
                    pass

            datos.append([
                item.get("fecha", ""),
                item.get("tipo_mantenimiento", ""),
                item.get("tecnico_proveedor", ""),
                equipos_str,
                item.get("descripcion", ""),
                f"${item.get('costo', 0.0)}",
                item.get("proximo_mantenimiento", "")
            ])
        return datos if datos else [["-", "Sin historial de mantenimientos", "-", "-", "-", "-", "-"]]
    except Exception as e: return [["Error", str(e), "-", "-", "-", "-", "-"]]

def cargar_historial_completo():
    try:
        res = supabase.table("mantenimientos").select("*").order("id", desc=True).execute()
        datos = []
        vistos = set() 
        for item in res.data:
            numero_informe = item.get("numero_informe")
            if numero_informe and numero_informe in vistos:
                continue
            if numero_informe:
                vistos.add(numero_informe)
            equipos_str = item.get("numero_serie", "")
            if numero_informe:
                try:
                    res_grupo = supabase.table("mantenimientos").select("numero_serie").eq("numero_informe", numero_informe).execute()
                    series_grupo = [g.get("numero_serie", "") for g in res_grupo.data if g.get("numero_serie")]
                    if series_grupo:
                        equipos_str = ", ".join(sorted(set(series_grupo)))
                except:
                    pass
            datos.append([
                item.get("fecha", ""),
                item.get("tipo_mantenimiento", ""),
                item.get("tecnico_proveedor", ""),
                equipos_str,
                item.get("descripcion", ""),
                f"${item.get('costo', 0.0)}",
                item.get("proximo_mantenimiento", "")
            ])
        return datos if datos else [["-", "No hay mantenimientos en el sistema", "-", "-", "-", "-", "-"]]
    except Exception as e:
        return [["Error", str(e), "-", "-", "-", "-", "-"]]

def obtener_mantenimientos_lista(serie_combo):
    try:
        if not serie_combo:
            return gr.update(choices=[], value=None)
        serie_limpia = str(serie_combo).split(" - ")[0].strip()
        return _mantenimientos_lista_por_series([serie_limpia])
    except Exception:
        return gr.update(choices=[], value=None)

def obtener_mantenimientos_lista_por_funcionario(funcionario_combo):
    try:
        if not funcionario_combo:
            return gr.update(choices=[], value=None)
        series_limpias = obtener_series_de_funcionario(funcionario_combo)
        if not series_limpias:
            return gr.update(choices=[], value=None)
        return _mantenimientos_lista_por_series(series_limpias)
    except Exception:
        return gr.update(choices=[], value=None)

def _mantenimientos_lista_por_series(series_limpias):
    try:
        res = supabase.table("mantenimientos").select("id, fecha, tipo_mantenimiento, numero_informe, numero_serie").in_("numero_serie", series_limpias).order("id", desc=True).execute()
        opciones = []
        vistos = set()
        for item in res.data:
            num = item.get("numero_informe") or f"ID-{item['id']}"
            if num in vistos:
                continue
            vistos.add(num)
            fecha = item.get("fecha", "")
            tipo = item.get("tipo_mantenimiento", "")
            opciones.append(f"{num} | {fecha} | {tipo} | id:{item['id']}")
        return gr.update(choices=opciones, value=None)
    except Exception:
        return gr.update(choices=[], value=None)

def descargar_reporte_mantenimiento(seleccion):
    if not seleccion:
        return gr.update(visible=False, value=None), "⚠️ Selecciona un registro primero."
    try:
        numero_informe = seleccion.split(" | ")[0].strip()
        res = supabase.table("mantenimientos").select("url_reporte_pdf").eq("numero_informe", numero_informe).limit(1).execute()
        if not res.data or not res.data[0].get("url_reporte_pdf"):
            return gr.update(visible=False, value=None), f"⚠️ El registro '{numero_informe}' no tiene un reporte PDF guardado."

        ruta_storage = res.data[0]["url_reporte_pdf"]
        contenido = supabase.storage.from_("reportes-mantenimiento").download(ruta_storage)

        temp_dir = tempfile.gettempdir()
        ruta_local = os.path.join(temp_dir, f"Reporte_{numero_informe}.pdf")
        with open(ruta_local, "wb") as f:
            f.write(contenido)

        return gr.update(value=ruta_local, visible=True), f"✅ Reporte {numero_informe} listo para descargar."
    except Exception as e:
        return gr.update(visible=False, value=None), f"❌ Error al descargar el reporte: {e}"

def eliminar_mantenimiento(seleccion, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not seleccion:
        return "⚠️ Selecciona un registro para eliminar."
    try:
        numero_informe = seleccion.split(" | ")[0].strip()
        id_referencia = int(seleccion.split("id:")[-1])

        if numero_informe and not numero_informe.startswith("ID-"):
            res_grupo = supabase.table("mantenimientos").select("id, url_reporte_pdf").eq("numero_informe", numero_informe).execute()
        else:
            res_grupo = supabase.table("mantenimientos").select("id, url_reporte_pdf").eq("id", id_referencia).execute()

        ids_a_borrar = [r["id"] for r in res_grupo.data]
        ruta_pdf_storage = next((r.get("url_reporte_pdf") for r in res_grupo.data if r.get("url_reporte_pdf")), None)

        if not ids_a_borrar:
            ids_a_borrar = [id_referencia]

        for id_reg in ids_a_borrar:
            supabase.table("mantenimientos").delete().eq("id", id_reg).execute()

        if ruta_pdf_storage:
            try:
                supabase.storage.from_("reportes-mantenimiento").remove([ruta_pdf_storage])
            except Exception:
                pass

        registrar_auditoria(usuario, f"Eliminó registro(s) de mantenimiento. Reporte: {numero_informe}.")
        return f"🗑️ Reporte '{numero_informe}' eliminado correctamente ({len(ids_a_borrar)} equipo(s) afectado(s))."
    except Exception as e:
        return f"❌ Error al eliminar: {e}"

def auto_completar_mantenimiento(serie_combo):
    try:
        if not serie_combo:
            return gr.update(choices=[], value=None), gr.update()
        
        if isinstance(serie_combo, list):
            if not serie_combo:
                return gr.update(choices=[], value=None), gr.update()
            serie_ref = serie_combo[-1] 
        else:
            serie_ref = serie_combo

        serie_limpia = str(serie_ref).split(" - ")[0].strip()
        historial = cargar_historial_mantenimientos(serie_combo)
        res = supabase.table("equipos").select("orden_id").ilike("numero_serie", f"{serie_limpia}%").execute()
        
        proveedor = ""
        if res.data and res.data[0].get("orden_id"):
            res_ord = supabase.table("ordenes_compra").select("razon_social_proveedor").eq("id", res.data[0]["orden_id"]).execute()
            if res_ord.data:
                proveedor = res_ord.data[0].get("razon_social_proveedor", "")

        return gr.update(choices=[proveedor] if proveedor else [], value=proveedor), gr.update(value=historial)

    except Exception as e:
        return gr.update(choices=[], value=None), gr.update()

def obtener_numero_reporte(proveedor):
    if not proveedor or str(proveedor).strip() == "" or str(proveedor).lower() == "mantenimiento interno":
        prefix = "INT"
    else:
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', str(proveedor))
        prefix = cleaned[:3].upper() if len(cleaned) >= 3 else cleaned.upper().ljust(3, 'X')
    
    for intento in range(3):
        try:
            res = supabase.table("mantenimientos").select("numero_informe").like("numero_informe", f"MNT-{prefix}-%").execute()
            max_num = 0
            for item in res.data:
                num_str = item.get("numero_informe", "")
                if num_str:
                    partes = num_str.split("-")
                    if len(partes) >= 3:
                        try:
                            num = int(partes[-1])
                            if num > max_num:
                                max_num = num
                        except ValueError:
                            pass
            siguiente = max_num + 1
            return f"MNT-{prefix}-{str(siguiente).zfill(4)}"
        except Exception as e:
            if intento < 2:
                time.sleep(1)
            else:
                raise Exception(f"Falla de red ({e})")

def registrar_y_generar_acta(funcionario_combo, serie_combo, fecha, tipo, checks, desc_extra, tecnico, costo, proximo,
                             fotos_paths, nombre_admin, nombre_tecnico_firma, nombre_funcionario_firma, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not funcionario_combo or not serie_combo or not fecha or not tipo:
        return "⚠️ El Funcionario, Serie, Fecha y Tipo son obligatorios.", [["-", "-", "-", "-", "-", "-", "-"]], gr.update(visible=False, value=None)

    try: nombre_custodio = str(funcionario_combo).split(" - ")[0].strip()
    except Exception: nombre_custodio = "Sin Asignar"

    trabajos = ", ".join(checks) if checks else ""
    desc_final = f"{trabajos}. {desc_extra}".strip(" .")

    def limpiar_texto(texto):
        if not texto: return ""
        texto = str(texto)
        reemplazos = {
            '●': '-', '•': '-', '·': '-', '◦': '-', '→': '->', '←': '<-', '✓': 'OK', '✗': 'X',
            '\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"', '\u2013': '-', '\u2014': '--', '\u2026': '...',
        }
        for orig, reemplazo in reemplazos.items(): texto = texto.replace(orig, reemplazo)
        return texto.encode('latin-1', errors='replace').decode('latin-1')

    mensajes = []
    
    try: numero_reporte = obtener_numero_reporte(tecnico)
    except Exception as error_red: return f"❌ INTERNET DESCONECTADO: No se pudo verificar la numeración en la BD. Revisa tu red.\nDetalle: {error_red}", gr.update(), gr.update(visible=False, value=None)

    series_limpias = []
    equipos_nombres = []
    ordenes_compra_encontradas = set()

    for s in serie_combo:
        serie_limpia = str(s).split(" - ")[0].strip()
        series_limpias.append(serie_limpia)
        equipos_nombres.append(s)

        try:
            res_eq_orden = supabase.table("equipos").select("orden_id, ordenes_compra(numero_orden_compra, numero_proceso_sercop)").ilike("numero_serie", f"{serie_limpia}%").execute()
            ord_info = None
            if res_eq_orden.data:
                ord_raw = res_eq_orden.data[0].get("ordenes_compra")
                if isinstance(ord_raw, list) and ord_raw: ord_info = ord_raw[0]
                elif isinstance(ord_raw, dict): ord_info = ord_raw

            if ord_info:
                num_orden = ord_info.get("numero_orden_compra") or ord_info.get("numero_proceso_sercop")
                if num_orden:
                    num_orden_normalizado = str(num_orden).strip()
                    if num_orden_normalizado: ordenes_compra_encontradas.add(num_orden_normalizado)
        except Exception: pass

        try:
            datos_mant = {
                "numero_serie": serie_limpia, "fecha": fecha, "tipo_mantenimiento": tipo, "descripcion": desc_final,
                "tecnico_proveedor": tecnico, "costo": float(costo) if costo else 0.0,
                "proximo_mantenimiento": proximo if proximo else None, "numero_informe": numero_reporte
            }
            supabase.table("mantenimientos").insert(datos_mant).execute()
            registrar_auditoria(usuario, f"Registró mantenimiento {tipo} al equipo {serie_limpia}. Reporte: {numero_reporte}")

            if tipo == "De Baja":
                supabase.table("equipos").update({"estado": "De Baja"}).ilike("numero_serie", f"{serie_limpia}%").execute()
            mensajes.append(f"✅ Registrado: {serie_limpia}")
        except Exception as e:
            mensajes.append(f"❌ Error en {serie_limpia}: {e}")

    ordenes_finales = []
    vistos_orden = set()
    for orden in sorted(ordenes_compra_encontradas):
        for parte in orden.split(","):
            parte_limpia = parte.strip()
            if parte_limpia and parte_limpia not in vistos_orden:
                vistos_orden.add(parte_limpia)
                ordenes_finales.append(parte_limpia)
    texto_orden_compra = ", ".join(ordenes_finales) if ordenes_finales else "No registrada"

    ruta_pdf = None
    
    if tipo in ["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"]:
        try:
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=20)
            pdf.add_page()
            pdf.set_fill_color(44, 62, 80)
            pdf.rect(0, 0, 210, 28, 'F')
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Arial", 'B', 16)
            pdf.set_xy(10, 8)
            pdf.cell(0, 10, limpiar_texto("REPORTE DE MANTENIMIENTO"), ln=True, align='C')
            pdf.set_font("Arial", '', 9)
            pdf.set_xy(10, 19)
            pdf.cell(0, 6, limpiar_texto(f"N. Reporte: {numero_reporte}"), ln=True, align='C')
            pdf.set_text_color(0, 0, 0)
            pdf.ln(8)

            pdf.set_font("Arial", 'B', 11)
            pdf.set_fill_color(220, 230, 241)
            pdf.cell(0, 8, "DATOS GENERALES", ln=True, fill=True)
            pdf.ln(2)

            campos = [
                ("Fecha de intervencion:", limpiar_texto(fecha)), ("Tipo de Mantenimiento:", limpiar_texto(tipo)),
                ("Empresa / Tecnico:", limpiar_texto(tecnico if tecnico else "Mantenimiento Interno")),
                ("Custodio a cargo:", limpiar_texto(nombre_custodio))
            ]
            for etiqueta, valor in campos:
                pdf.set_font("Arial", 'B', 10)
                pdf.cell(65, 7, limpiar_texto(etiqueta), 0, 0)
                pdf.set_font("Arial", '', 10)
                pdf.cell(0, 7, valor, 0, 1)

            pdf.ln(4)
            pdf.set_font("Arial", 'B', 11)
            pdf.set_fill_color(220, 230, 241)
            pdf.cell(0, 8, "EQUIPOS INTERVENIDOS", ln=True, fill=True)
            pdf.ln(2)
            pdf.set_font("Arial", '', 10)
            for eq in equipos_nombres: pdf.cell(0, 6, limpiar_texto(f"- {eq}"), ln=True)
            pdf.ln(4)

            pdf.set_font("Arial", 'B', 11)
            pdf.set_fill_color(220, 230, 241)
            pdf.cell(0, 8, "TRABAJOS REALIZADOS", ln=True, fill=True)
            pdf.ln(2)
            pdf.set_font("Arial", '', 10)
            texto_trabajos = trabajos.strip(" .") if trabajos else "No se especificaron trabajos puntuales."
            pdf.multi_cell(0, 6, limpiar_texto(texto_trabajos))
            pdf.ln(4)

            if desc_extra and desc_extra.strip():
                pdf.set_font("Arial", 'B', 11)
                pdf.set_fill_color(220, 230, 241)
                pdf.cell(0, 8, "CONCLUSIONES / RECOMENDACIONES", ln=True, fill=True)
                pdf.ln(2)
                pdf.set_font("Arial", '', 10)
                pdf.multi_cell(0, 6, limpiar_texto(desc_extra.strip()))
                pdf.ln(4)

            lista_fotos = []
            if fotos_paths:
                if isinstance(fotos_paths, list): lista_fotos = [f for f in fotos_paths if f]
                elif isinstance(fotos_paths, str) and fotos_paths: lista_fotos = [fotos_paths]

            if lista_fotos:
                pdf.set_font("Arial", 'B', 11)
                pdf.set_fill_color(220, 230, 241)
                pdf.cell(0, 8, "EVIDENCIA FOTOGRAFICA", ln=True, fill=True)
                pdf.ln(2)

                ancho_foto = 88
                margen_izq = 10
                espacio_entre = 5
                margen_inferior = 20
                alto_pagina = 297  
                x_inicio = [margen_izq, margen_izq + ancho_foto + espacio_entre]

                from PIL import Image as PILImage
                def alto_real(ruta, ancho_mm):
                    try:
                        with PILImage.open(ruta) as img:
                            w_px, h_px = img.size
                        return ancho_mm * (h_px / w_px)
                    except Exception: return 68

                col, y_fila, alto_fila_actual = 0, pdf.get_y(), 0
                for i, foto_path in enumerate(lista_fotos):
                    alto_img = alto_real(foto_path, ancho_foto)
                    if col == 0:
                        if pdf.get_y() + alto_img + 10 > alto_pagina - margen_inferior: pdf.add_page()
                        y_fila = pdf.get_y()
                        alto_fila_actual = alto_img
                    else: alto_fila_actual = max(alto_fila_actual, alto_img)

                    x = x_inicio[col]
                    try: pdf.image(foto_path, x=x, y=y_fila, w=ancho_foto)
                    except Exception:
                        pdf.set_xy(x, y_fila)
                        pdf.set_font("Arial", 'I', 9)
                        pdf.cell(ancho_foto, 6, limpiar_texto(f"(Error foto {i+1})"), 0, 0)
                    col += 1
                    if col == 2:
                        col = 0
                        pdf.set_y(y_fila + alto_fila_actual + 6)
                        pdf.ln(2)
                if col != 0: pdf.set_y(y_fila + alto_fila_actual + 6)
                pdf.ln(6)

            espacio_necesario = 60
            if pdf.get_y() + espacio_necesario > 270: pdf.add_page()

            pdf.ln(10)
            pdf.set_font("Arial", 'B', 11)
            pdf.set_fill_color(220, 230, 241)
            pdf.cell(0, 8, "FIRMAS DE CONFORMIDAD", ln=True, fill=True)
            pdf.ln(12)

            y_firma = pdf.get_y()
            ancho_firma = 55
            posiciones_x = [12, 78, 144]
            nombres_firma = [
                limpiar_texto(nombre_admin if nombre_admin else "Administrador de la Orden de Compra"),
                limpiar_texto(nombre_funcionario_firma if nombre_funcionario_firma else "Funcionario a Cargo"),
                limpiar_texto(nombre_tecnico_firma if nombre_tecnico_firma else "Tecnico del Proveedor"),
            ]
            roles_firma = ["Administrador de la Orden de Compra", "Funcionario a Cargo del Equipo", "Tecnico del Proveedor"]
            detalle_extra_firma = [f"{limpiar_texto(texto_orden_compra)}", "", ""]

            for i, x in enumerate(posiciones_x):
                pdf.line(x, y_firma, x + ancho_firma, y_firma)
                pdf.set_xy(x, y_firma + 2)
                pdf.set_font("Arial", 'B', 9)
                pdf.cell(ancho_firma, 5, nombres_firma[i], 0, 2, 'C')
                pdf.set_font("Arial", 'I', 8)
                pdf.set_x(x)
                pdf.cell(ancho_firma, 4, limpiar_texto(roles_firma[i]), 0, 2, 'C')
                if detalle_extra_firma[i]:
                    pdf.set_font("Arial", '', 7)
                    pdf.set_x(x)
                    pdf.cell(ancho_firma, 4, detalle_extra_firma[i], 0, 0, 'C')

            temp_dir = tempfile.gettempdir()
            ruta_pdf = os.path.join(temp_dir, f"Reporte_{numero_reporte}.pdf")
            pdf.output(ruta_pdf)

            try:
                ruta_storage = f"{numero_reporte}/Reporte_{numero_reporte}.pdf"
                with open(ruta_pdf, "rb") as f:
                    supabase.storage.from_("reportes-mantenimiento").upload(ruta_storage, f, file_options={"content-type": "application/pdf", "upsert": "true"})
                supabase.table("mantenimientos").update({"url_reporte_pdf": ruta_storage}).eq("numero_informe", numero_reporte).execute()
            except Exception as e_storage: mensajes.append(f"⚠️ El PDF se generó pero no se pudo guardar en el historial: {e_storage}")
        except Exception as e: mensajes.append(f"❌ Error al crear PDF: {e}")

    resumen = "\n".join(mensajes)
    historial_actualizado = cargar_historial_por_funcionario(funcionario_combo) if funcionario_combo else [["-", "-", "-", "-", "-", "-", "-"]]
    if ruta_pdf: return f"✅ Reporte {numero_reporte} generado.\n{resumen}", gr.update(value=historial_actualizado), gr.update(value=ruta_pdf, visible=True)
    else: return f"✅ Operación finalizada.\n{resumen}", gr.update(value=historial_actualizado), gr.update(visible=False, value=None)

def cargar_todo_ui():
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        f_inv = executor.submit(lambda: cargar_datos_inventario(as_styled=True))
        f_sd = executor.submit(obtener_series_disponibles)
        f_fu = executor.submit(obtener_funcionarios)
        f_sa = executor.submit(obtener_series_asignadas)
        f_st = executor.submit(obtener_todas_las_series)
        f_lf = executor.submit(cargar_listado_funcionarios)
        f_ul = executor.submit(obtener_usuarios_lista)
        f_fm = executor.submit(obtener_funcionarios_mantenimiento)
        
        return (f_inv.result(), f_sd.result(), f_fu.result(), f_sa.result(), f_st.result(), f_lf.result(), f_fu.result(), f_ul.result(), f_fm.result())

def inicializar_sistema_completo(request: gr.Request):
    usuario = request.username if request else ""
    es_admin = False
    try:
        res_rol = supabase.table("usuarios_sistema").select("rol").eq("usuario", usuario).execute()
        if res_rol.data and res_rol.data[0].get('rol') == 'admin': es_admin = True
    except: pass
    
    panel_seg = gr.update(visible=es_admin)
    panel_den = gr.update(visible=not es_admin)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        f_aud = executor.submit(cargar_datos_auditoria)
        f_usr = executor.submit(cargar_usuarios_sistema)
        f_inv = executor.submit(lambda: cargar_datos_inventario(as_styled=True))
        f_sd = executor.submit(obtener_series_disponibles)
        f_fu = executor.submit(obtener_funcionarios)
        f_sa = executor.submit(obtener_series_asignadas)
        f_st = executor.submit(obtener_todas_las_series)
        f_lf = executor.submit(cargar_listado_funcionarios)
        f_ul = executor.submit(obtener_usuarios_lista)
        f_fm = executor.submit(obtener_funcionarios_mantenimiento)
        
        return (f_aud.result(), f_usr.result(), f_inv.result(), f_sd.result(), f_fu.result(), f_sa.result(), f_st.result(), f_lf.result(), f_fu.result(), f_ul.result(), f_fm.result(), panel_seg, panel_den, f_fm.result())

def seleccionar_funcionario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        if tabla_actual.empty: return "", "", "", "", None
        fila = evt.index[0]
        return str(tabla_actual.iloc[fila, 0]), str(tabla_actual.iloc[fila, 1]), str(tabla_actual.iloc[fila, 2]), str(tabla_actual.iloc[fila, 3]), f"{str(tabla_actual.iloc[fila, 1])} - {str(tabla_actual.iloc[fila, 0])}"
    except Exception: return "", "", "", "", None

def seleccionar_usuario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        if tabla_actual.empty: return "", "admin", None
        fila = evt.index[0]
        usr = str(tabla_actual.iloc[fila, 0])
        rol = str(tabla_actual.iloc[fila, 1]).lower()
        if rol not in ["admin", "operador"]: rol = "admin"
        return usr, rol, f"{usr} - {rol}"
    except Exception: return "", "admin", None

def seleccionar_inventario_tabla(evt: gr.SelectData, tabla_actual):
    try:
        if tabla_actual is None or len(tabla_actual) == 0: return None, "", "", None
        if isinstance(tabla_actual, pd.DataFrame):
            fila = evt.index[0]
            tipo = str(tabla_actual.iloc[fila, 0])
            serie = str(tabla_actual.iloc[fila, 2])
            custodio = str(tabla_actual.iloc[fila, 3])
            estado = str(tabla_actual.iloc[fila, 5]) 
        else:
            fila = evt.index[0]
            tipo = str(tabla_actual[fila][0])
            serie = str(tabla_actual[fila][2])
            custodio = str(tabla_actual[fila][3])
            estado = str(tabla_actual[fila][5])
            
        return f"{serie} - {tipo}", estado, tipo, (f"{serie} - {tipo} ({custodio})" if custodio != "Sin Asignar" else None)
    except Exception: return None, "", "", None

def cargar_datos_funcionario_form(funcionario_combo):
    if not funcionario_combo: return "", "", "", ""
    cedula_limpia = str(funcionario_combo).split(" - ")[-1].strip()
    try:
        res = supabase.table("funcionarios").select("*").eq("cedula", cedula_limpia).execute()
        if res.data:
            f = res.data[0]
            return f.get("cedula", ""), f.get("nombres_completos", ""), f.get("cargo", ""), f.get("departamento", "")
        return "", "", "", ""
    except: return "", "", "", ""

def cargar_datos_usuario_form(usuario_combo):
    if not usuario_combo: return "", "admin"
    usuario_limpio = str(usuario_combo).split(" - ")[0].strip()
    try:
        res = supabase.table("usuarios_sistema").select("*").eq("usuario", usuario_limpio).execute()
        if res.data: return res.data[0].get("usuario", ""), res.data[0].get("rol", "admin")
        return "", "admin"
    except: return "", "admin"

def analizar_acta_pdf(archivo_acta):
    if not archivo_acta: return None, {}, "⚠️ Sube un Acta primero."
    try:
        reader = PdfReader(archivo_acta)
        texto_acta = "".join(pagina.extract_text() + "\n" for pagina in reader.pages)
        prompt = f"""Lee el Acta. Extrae los datos y devuelve UNICAMENTE un objeto JSON puro (sin comillas invertidas ni bloques markdown).
ESTRUCTURA EXACTA Y OBLIGATORIA DEL JSON:
{{
    "numero_proceso_sercop": "Escribe aquí el proceso",
    "numero_orden_compra": "Escribe aquí la orden",
    "razon_social_proveedor": "ESCRIBE_AQUI_EL_NOMBRE_DEL_PROVEEDOR",
    "nombre_comercial_proveedor": "Opcional: Si el acta menciona un nombre comercial distinto a la razón social, ponlo aquí",
    "objeto_contratacion": "Escribe aquí el objeto",
    "monto": 0.0,
    "equipos": [
        {{"tipo": "Ej: Laptop", "marca": "Ej: Dell", "modelo": "Ej: Latitude", "serie": "Ej: 12345", "observaciones": "Cualquier detalle extra (opcional)"}}
    ]
}}
REGLA VITAL: Debes extraer TODOS Y CADA UNO de los equipos físicos mencionados en el acta. No omitas ninguno.
Texto del acta: {texto_acta}"""
        respuesta = llamar_gemini_con_reintentos(prompt)
        texto_json = respuesta.text.strip()
        marcador = "`" + "`" + "`"
        if texto_json.startswith(marcador + "json"): texto_json = texto_json[7:]
        if texto_json.startswith(marcador): texto_json = texto_json[3:]
        if texto_json.endswith(marcador): texto_json = texto_json[:-3]
        
        datos = json.loads(texto_json.strip())
        proveedor = datos.get("razon_social_proveedor", "No especificado")
        nombre_comercial = datos.get("nombre_comercial_proveedor", "")
        orden = datos.get("numero_orden_compra", "Sin CE")
        proceso = datos.get("numero_proceso_sercop", "Sin Proceso")
        
        filas = []
        for eq in datos.get("equipos", []):
            filas.append([
                eq.get("tipo", ""), eq.get("marca", ""), eq.get("modelo", ""), eq.get("serie", ""),
                proveedor, nombre_comercial, orden, proceso, eq.get("observaciones", "")
            ])
        return filas, datos, "✅ Acta leída. Revisa y edita los datos en la tabla de abajo, y luego presiona '2. Confirmar y Guardar'."
    except Exception as e: return None, {}, f"❌ Error leyendo PDF: {e}"

def procesar_acta_recepcion(tabla_datos, state_datos, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if tabla_datos is None or tabla_datos.empty: 
        return "⚠️ La tabla está vacía. Analiza el acta primero.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()
    try:
        proveedor_extraido = str(tabla_datos.iloc[0, 4]).strip()
        nombre_comercial_extraido = str(tabla_datos.iloc[0, 5]).strip()
        num_orden = str(tabla_datos.iloc[0, 6]).strip()
        num_proceso = str(tabla_datos.iloc[0, 7]).strip()
        
        resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", num_proceso).eq("numero_orden_compra", num_orden).execute()
        if not resp_orden.data: resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{num_proceso} [Ref: {num_orden}]").eq("numero_orden_compra", num_orden).execute()
        
        orden_id = None
        if resp_orden.data:
            orden_id = resp_orden.data[0]['id']
            supabase.table("ordenes_compra").update({"razon_social_proveedor": proveedor_extraido, "nombre_comercial": nombre_comercial_extraido}).eq("id", orden_id).execute()
        else:
            proceso_a_guardar = num_proceso
            if num_proceso and num_proceso not in ["Sin Proceso", "-", "nan", ""]:
                check_proceso = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", num_proceso).execute()
                if check_proceso.data: proceso_a_guardar = f"{num_proceso} [Ref: {num_orden}]"
            if not proceso_a_guardar or proceso_a_guardar in ["Sin Proceso", "-", "nan", ""]: proceso_a_guardar = f"MANUAL-{int(time.time())}"
                
            nueva_orden = {
                "numero_proceso_sercop": proceso_a_guardar, "numero_orden_compra": num_orden, 
                "razon_social_proveedor": proveedor_extraido, "nombre_comercial": nombre_comercial_extraido,
                "objeto_contratacion": state_datos.get("objeto_contratacion", "Adquisición automática por Acta"), 
                "monto": float(state_datos.get("monto", 0.0)) if state_datos.get("monto") else 0.0, "fecha_adquisicion": "2023-01-01"
            }
            res_insert = supabase.table("ordenes_compra").insert(nueva_orden).execute()
            orden_id = res_insert.data[0]['id']
            
        res_todos = supabase.table("equipos").select("id, numero_serie").execute()
        mapa_equipos_db = {}
        for eq_db in res_todos.data:
            if eq_db.get("numero_serie"):
                s_limpia = str(eq_db["numero_serie"]).strip().upper().replace(" ", "")
                mapa_equipos_db[s_limpia] = eq_db["id"]

        registros_nuevos = 0
        registros_actualizados = 0

        for index, row in tabla_datos.iterrows():
            tipo = str(row.iloc[0]).strip()
            marca = str(row.iloc[1]).strip()
            modelo = str(row.iloc[2]).strip()
            serie_acta = str(row.iloc[3]).strip()
            observaciones = str(row.iloc[8]).strip()
            
            if not serie_acta or serie_acta.lower() == 'nan': continue

            serie_limpia_acta = serie_acta.upper().replace(" ", "")
            equipo_id_existente = mapa_equipos_db.get(serie_limpia_acta)

            try:
                if equipo_id_existente:
                    supabase.table("equipos").update({"orden_id": orden_id, "tipo_equipo": tipo, "marca": marca, "modelo": modelo, "observaciones": observaciones}).eq("id", equipo_id_existente).execute()
                    registros_actualizados += 1
                else:
                    supabase.table("equipos").insert({"orden_id": orden_id, "tipo_equipo": tipo, "marca": marca, "modelo": modelo, "numero_serie": serie_acta, "estado": "Operativo", "observaciones": observaciones}).execute()
                    registros_nuevos += 1
            except Exception as e: print(f"Error vinculando serie {serie_acta}: {e}")
                
        registrar_auditoria(usuario, f"Ingresó acta. {registros_nuevos} creados, {registros_actualizados} actualizados.")
        mensaje_interfaz = f"✅ Éxito: {registros_nuevos} equipos NUEVOS y {registros_actualizados} ACTUALIZADOS.\n🏢 Proveedor: {proveedor_extraido}"
        return mensaje_interfaz, cargar_datos_inventario(as_styled=True), obtener_series_disponibles()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()

def ingresar_equipo_manual(proceso, orden, proveedor, nombre_comercial, objeto, monto, tipo, marca, modelo, serie, observaciones, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not orden or not serie: return "⚠️ Faltan datos.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()

    orden = str(orden).strip()
    proceso_limpio = str(proceso).strip() if proceso else ""

    try:
        resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proceso_limpio).eq("numero_orden_compra", orden).execute()
        if not resp_orden.data: resp_orden = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", f"{proceso_limpio} [Ref: {orden}]").eq("numero_orden_compra", orden).execute()
            
        orden_id = None
        if resp_orden.data:
            orden_id = resp_orden.data[0]['id']
            actualizaciones = {}
            if proveedor: actualizaciones["razon_social_proveedor"] = proveedor
            if nombre_comercial: actualizaciones["nombre_comercial"] = nombre_comercial
            if actualizaciones: supabase.table("ordenes_compra").update(actualizaciones).eq("id", orden_id).execute()
        else:
            proceso_a_guardar = proceso_limpio
            if proceso_limpio and proceso_limpio not in ["Sin Proceso", "-", "nan", ""]:
                check_proceso = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", proceso_limpio).execute()
                if check_proceso.data: proceso_a_guardar = f"{proceso_limpio} [Ref: {orden}]"
            if not proceso_a_guardar or proceso_a_guardar in ["Sin Proceso", "-", "nan", ""]: proceso_a_guardar = f"MANUAL-{int(time.time())}"
                
            nueva_orden = {
                "numero_proceso_sercop": proceso_a_guardar, "numero_orden_compra": orden,
                "razon_social_proveedor": proveedor or "No especificado", "nombre_comercial": nombre_comercial or "",
                "objeto_contratacion": objeto or "Adquisición Manual", "monto": float(monto) if monto else 0.0,
                "fecha_adquisicion": "2023-01-01"
            }
            res_insert = supabase.table("ordenes_compra").insert(nueva_orden).execute()
            orden_id = res_insert.data[0]['id']

        supabase.table("equipos").insert({"orden_id": orden_id, "tipo_equipo": tipo or "No definido", "marca": marca or "No definida", "modelo": modelo or "No definido", "numero_serie": serie, "estado": "Operativo", "observaciones": observaciones or ""}).execute()
        registrar_auditoria(usuario, f"Ingresó equipo manual {serie}.")
        return f"✅ Equipo '{serie}' ingresado.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()

    except Exception as e:
        if '23505' in str(e):
            if 'numero_serie' in str(e) or 'serie' in str(e).lower(): return f"❌ Error: La serie '{serie}' ya existe.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()
            return f"❌ Error: Ya existe una orden con ese Proceso SERCOP u Orden de Compra. Detalle: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()
        return f"❌ Error: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles()

def registrar_y_actualizar(cedula, nombres, cargo, departamento, request: gr.Request):
    usuario = request.username if request else "Sistema"
    cedula_limpia = cedula.strip()
    if not cedula_limpia or not nombres: return "⚠️ Faltan datos.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        res = supabase.table("funcionarios").select("id").eq("cedula", cedula_limpia).execute()
        if res.data:
            supabase.table("funcionarios").update({"nombres_completos": nombres, "cargo": cargo, "departamento": departamento}).eq("cedula", cedula_limpia).execute()
            registrar_auditoria(usuario, f"Actualizó datos de {nombres}.")
            mensaje = f"✅ Datos actualizados."
        else:
            supabase.table("funcionarios").insert({"cedula": cedula_limpia, "nombres_completos": nombres, "cargo": cargo, "departamento": departamento}).execute()
            registrar_auditoria(usuario, f"Registró a {nombres}.")
            mensaje = f"✅ Funcionario registrado."
        return mensaje, obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def eliminar_funcionario(cedula, request: gr.Request):
    usuario = request.username if request else "Sistema"
    cedula_limpia = cedula.strip() if cedula else ""
    if not cedula_limpia: return "⚠️ Debes ingresar o seleccionar el Código del GAD del funcionario a eliminar.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    try:
        res_func = supabase.table("funcionarios").select("id, nombres_completos").eq("cedula", cedula_limpia).execute()
        if not res_func.data: return "⚠️ Funcionario no encontrado en la base de datos.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
        func_id = res_func.data[0]['id']
        nombres = res_func.data[0]['nombres_completos']
        
        res_eq = supabase.table("equipos").select("numero_serie, tipo_equipo, marca").eq("funcionario_id", func_id).execute()
        if res_eq.data and len(res_eq.data) > 0:
            detalle = "\n".join([f"   • {eq.get('numero_serie', '-')} ({eq.get('tipo_equipo', '') or 'Sin tipo'} {eq.get('marca', '') or ''})".strip() for eq in res_eq.data])
            return (
                f"❌ ALERTA: No se puede eliminar a {nombres} porque tiene {len(res_eq.data)} equipo(s) a su cargo:\n{detalle}\n\n"
                f"Ve a la pestaña '2. Custodios e Inventario' → Acordeón '6. Edición Masiva y Limpieza', libera sus equipos a estado 'Sin Asignar'. Luego vuelve a intentar eliminar a {nombres}.",
                obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
            )
        supabase.table("funcionarios").delete().eq("cedula", cedula_limpia).execute()
        registrar_auditoria(usuario, f"Eliminó al funcionario: {nombres} ({cedula_limpia}).")
        return f"🗑️ Funcionario {nombres} eliminado correctamente.", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()
    except Exception as e: return f"❌ Error al eliminar: {e}", obtener_funcionarios(), cargar_listado_funcionarios(), obtener_funcionarios()

def asignar_custodio_equipo(serie_combo, funcionario_combo, archivo_garantia, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_combo or not funcionario_combo: 
        return "⚠️ Faltan selecciones.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    
    serie_limpia = serie_combo.split(" - ")[0].strip()
    cedula_limpia = str(funcionario_combo).split(" - ")[-1].strip()
    nombre_custodio = str(funcionario_combo).split(" - ")[0].strip()
    
    try:
        res_func = supabase.table("funcionarios").select("id").eq("cedula", cedula_limpia).execute()
        if not res_func.data: return "❌ Error: Funcionario no encontrado.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
            
        func_id = res_func.data[0]['id']
        res_eq = supabase.table("equipos").select("id, numero_serie").ilike("numero_serie", f"{serie_limpia}%").execute()
        
        if not res_eq.data: return f"❌ Error: El equipo {serie_limpia} no fue encontrado en la base de datos.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()

        equipo_id = res_eq.data[0]['id']
        serie_real = res_eq.data[0]['numero_serie']
        
        supabase.table("equipos").update({"funcionario_id": func_id}).eq("id", equipo_id).execute()

        mensaje_final = f"✅ Equipo {serie_real} asignado a {nombre_custodio}."
        registrar_auditoria(usuario, f"Asignó {serie_real} a {nombre_custodio}.")
        
        if archivo_garantia:
            texto_garantia = "".join(pagina.extract_text() + "\n" for pagina in PdfReader(archivo_garantia).pages)
            res_ia = llamar_gemini_con_reintentos(f"Extrae tiempo de garantía: {texto_garantia}")
            mensaje_final += f"\n\n📜 GARANTÍA:\n{res_ia.text.strip()}"
            
        return mensaje_final, cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_funcionarios(), obtener_series_asignadas()

def liberar_equipo(serie_liberar_combo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_liberar_combo: return "⚠️ Selecciona un equipo.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas()
    serie_limpia = serie_liberar_combo.split(" - ")[0].strip()
    try:
        res_eq = supabase.table("equipos").select("id, numero_serie").ilike("numero_serie", f"{serie_limpia}%").execute()
        if res_eq.data:
            supabase.table("equipos").update({"funcionario_id": None}).eq("id", res_eq.data[0]['id']).execute()
            registrar_auditoria(usuario, f"Liberó equipo {res_eq.data[0]['numero_serie']}.")
            return f"🔓 Equipo {res_eq.data[0]['numero_serie']} liberado.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas()
        return "❌ Error: Equipo no encontrado.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas()
    except Exception as e: return f"❌ Error: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas()

def eliminar_equipo_inventario(serie_eliminar_combo, motivo, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_eliminar_combo: return "⚠️ Selecciona un equipo para dar de baja.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
    if not motivo or not str(motivo).strip(): return "⚠️ OBLIGATORIO: Debes escribir el motivo por el cual das de baja o eliminas este bien.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()

    serie_limpia = str(serie_eliminar_combo).split(" - ")[0].strip()
    try:
        res = supabase.table("equipos").select("id, tipo_equipo, marca, modelo, numero_serie, estado, observaciones, funcionario_id").ilike("numero_serie", f"{serie_limpia}%").execute()
        if not res.data: return f"❌ El equipo '{serie_limpia}' no fue encontrado en la base de datos.", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()

        equipo = res.data[0]
        serie_real = equipo['numero_serie']

        if equipo.get("funcionario_id") is not None:
            return (
                f"❌ No se puede dar de baja: el equipo '{serie_real}' todavía tiene un custodio asignado.\nPrimero libéralo en la sección de arriba y luego vuelve a intentarlo.",
                cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()
            )

        obs_actual = equipo.get("observaciones") or ""
        nueva_obs = f"[ELIMINADO / DE BAJA - Motivo: {str(motivo).strip()}] {obs_actual}".strip()
        
        supabase.table("equipos").update({"estado": "De Baja", "observaciones": nueva_obs, "funcionario_id": None}).eq("id", equipo["id"]).execute()
        registrar_auditoria(usuario, f"🗑️ DIO DE BAJA el equipo '{serie_real}'. Motivo: {motivo}")

        mensaje = f"🗑️ Equipo '{serie_real}' dado de baja correctamente.\n✅ El equipo ahora se mostrará en color rojo y el motivo ha sido añadido a sus observaciones."
        return mensaje, cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update(value="")
    except Exception as e: return f"❌ Error al eliminar: {e}", cargar_datos_inventario(as_styled=True), obtener_series_disponibles(), obtener_series_asignadas(), obtener_todas_las_series(), cargar_datos_auditoria(), gr.update()

def advertencia_asignacion(funcionario_combo):
    if not funcionario_combo: return ""
    try:
        res_func = supabase.table("funcionarios").select("id").eq("cedula", funcionario_combo.split(" - ")[-1]).execute()
        if res_func.data:
            res_eq = supabase.table("equipos").select("tipo_equipo").eq("funcionario_id", res_func.data[0]['id']).execute()
            if len(res_eq.data) > 0: return f"⚠️ ADVERTENCIA: Esta persona ya tiene {len(res_eq.data)} equipo(s)."
        return "ℹ️ Sin equipos asignados."
    except Exception: return ""

def analizar_excel_masivo(archivo_excel):
    if not archivo_excel: return "⚠️ Sube un archivo de Excel.", []
    try:
        df_raw = pd.read_excel(archivo_excel, header=None)
        header_idx = -1
        for i, row in df_raw.iterrows():
            row_str = " ".join(str(val).lower() for val in row.values)
            if 'serie' in row_str and ('nombre' in row_str or 'funcionario' in row_str or 'custodio' in row_str):
                header_idx = i
                break
                
        if header_idx == -1: return "❌ Error: No se encontró la tabla de datos.", []
            
        df = pd.read_excel(archivo_excel, header=header_idx)
        df.columns = df.columns.astype(str).str.strip().str.lower()
        
        col_serie = next((c for c in df.columns if 'serie' in c), None)
        col_cedula = next((c for c in df.columns if 'cedula' in c or 'cédula' in c or 'identificacion' in c or 'cód' in c or 'cod' in c or 'codigo' in c), None)
        col_nombres = next((c for c in df.columns if 'nombre' in c or 'funcionario' in c or 'custodio' in c), None)
        col_cargo = next((c for c in df.columns if 'cargo' in c), None)
        col_depto = next((c for c in df.columns if 'departamento' in c or 'area' in c or 'dirección' in c), None)

        res_all_eq = supabase.table("equipos").select("numero_serie").execute()
        equipos_db = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in res_all_eq.data}
            
        res_all_func = supabase.table("funcionarios").select("cedula, nombres_completos").execute()
        funcionarios_db_cedulas = {str(f.get("cedula", "")).strip(): True for f in res_all_func.data}
        funcionarios_db_nombres = {str(f.get("nombres_completos", "")).strip().upper(): str(f.get("cedula", "")).strip() for f in res_all_func.data}

        vista_previa = []
        filas_leidas = 0

        for index, row in df.iterrows():
            nombres = str(row[col_nombres]).strip() if col_nombres and pd.notna(row[col_nombres]) else "Sin Nombre"
            serie_excel = str(row[col_serie]) if col_serie and pd.notna(row[col_serie]) else ""
            serie_limpia = serie_excel.strip().upper().replace(" ", "")
            if serie_limpia.endswith('.0'): serie_limpia = serie_limpia[:-2]
            
            cedula_excel = str(row[col_cedula]) if col_cedula and pd.notna(row[col_cedula]) else ""
            cedula = cedula_excel.strip()
            if cedula.endswith('.0'): cedula = cedula[:-2]
            
            if not cedula or cedula.lower() == 'nan':
                for val in row.values:
                    v_str = str(val).strip()
                    if v_str.endswith('.0'): v_str = v_str[:-2]
                    if v_str.isdigit() and len(v_str) in [9, 10]:
                        cedula = v_str
                        break
                        
            if len(cedula) == 9 and cedula.isdigit(): cedula = "0" + cedula
                
            if (not cedula or cedula.lower() == 'nan') and nombres and nombres != "Sin Nombre":
                cedula_encontrada = funcionarios_db_nombres.get(nombres.upper())
                if cedula_encontrada: cedula = cedula_encontrada

            cargo = str(row[col_cargo]).strip() if col_cargo and pd.notna(row[col_cargo]) else "No definido"
            departamento = str(row[col_depto]).strip() if col_depto and pd.notna(row[col_depto]) else "No definido"

            if (not serie_limpia or serie_limpia == 'NAN') and (not cedula or cedula.lower() == 'nan') and nombres == "Sin Nombre": continue
                
            filas_leidas += 1

            serie_mostrar = serie_limpia if serie_limpia and serie_limpia != 'NAN' else ""
            cedula_mostrar = cedula if cedula and cedula.lower() != 'nan' else ""

            if not serie_mostrar:
                vista_previa.append(["", cedula_mostrar, nombres, cargo, departamento, "❌ ERROR: Falta número de serie"])
                continue
                
            serie_exacta_db = equipos_db.get(serie_mostrar)
            if not serie_exacta_db:
                vista_previa.append([serie_mostrar, cedula_mostrar, nombres, cargo, departamento, "❌ ERROR: Este equipo NO existe en BD"])
                continue

            if not cedula_mostrar:
                vista_previa.append([serie_exacta_db, "", nombres, cargo, departamento, "❌ ERROR: Falta Código del GAD"])
                continue

            es_nuevo = cedula_mostrar not in funcionarios_db_cedulas
            vista_previa.append([serie_exacta_db, cedula_mostrar, nombres, cargo, departamento, "⚠️ OK (Se creará funcionario)" if es_nuevo else "✅ OK"])

        if filas_leidas == 0: return "⚠️ El archivo está vacío.", []

        errores_encontrados = sum(1 for fila in vista_previa if "❌" in fila[-1])
        correctos = len(vista_previa) - errores_encontrados
        msg = f"✅ Análisis listo. {correctos} listos para asignar. (Revisa/edita las filas con ❌ ERROR en la tabla antes de confirmar)."
        return msg, vista_previa

    except Exception as e: return f"❌ Error: {str(e)}", []

def confirmar_asignacion_masiva(tabla_datos, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if tabla_datos is None or tabla_datos.empty: return "⚠️ No hay datos en la tabla."
    exitos, func_creados, errores = 0, 0, 0
    
    res_all_func = supabase.table("funcionarios").select("cedula, id").execute()
    mapa_funcionarios = {str(f["cedula"]).strip(): f["id"] for f in res_all_func.data}

    res_all_eq = supabase.table("equipos").select("numero_serie").execute()
    equipos_db_validos = {str(eq.get("numero_serie", "")).strip().upper().replace(" ", ""): str(eq.get("numero_serie", "")) for eq in res_all_eq.data}

    for index, row in tabla_datos.iterrows():
        try:
            serie_raw = str(row.iloc[0]).strip()
            cedula_raw = str(row.iloc[1]).strip()
            nombres_raw = str(row.iloc[2]).strip()
            cargo_raw = str(row.iloc[3]).strip()
            depto_raw = str(row.iloc[4]).strip()
            
            if not serie_raw or serie_raw.lower() == 'nan' or not cedula_raw or cedula_raw.lower() == 'nan':
                errores += 1
                continue
                
            serie_limpia = serie_raw.upper().replace(" ", "")
            serie_exacta = equipos_db_validos.get(serie_limpia)
            
            if not serie_exacta:
                errores += 1
                continue
            
            func_id = mapa_funcionarios.get(cedula_raw)
            
            if not func_id:
                res_insert = supabase.table("funcionarios").insert({
                    "cedula": cedula_raw, "nombres_completos": nombres_raw, 
                    "cargo": cargo_raw, "departamento": depto_raw
                }).execute()
                if res_insert.data:
                    func_id = res_insert.data[0]["id"]
                    mapa_funcionarios[cedula_raw] = func_id
                    func_creados += 1
            
            if func_id:
                supabase.table("equipos").update({"funcionario_id": func_id}).eq("numero_serie", serie_exacta).execute()
                exitos += 1
        except Exception: 
            errores += 1
        
    registrar_auditoria(usuario, f"Carga masiva desde tabla: {exitos} asignados.")
    mensaje = f"✅ Completado: {exitos} equipos asignados, {func_creados} funcionarios nuevos creados."
    if errores > 0:
        mensaje += f" (⚠️ Se omitieron {errores} filas por datos incompletos o series inválidas)."
    return mensaje

def cargar_tabla_edicion(termino=""):
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        datos = []
        for eq in res.data:
            proveedor = ""
            nombre_comercial = ""
            ord_data = eq.get("ordenes_compra")
            if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
            if ord_data: 
                proveedor = ord_data.get("razon_social_proveedor", "")
                nombre_comercial = ord_data.get("nombre_comercial", "")

            func = eq.get("funcionarios")
            if isinstance(func, list) and func: func = func[0]
            elif not isinstance(func, dict): func = {}

            custodio = func.get("nombres_completos") if func else "Sin Asignar"
            depto = func.get("departamento") if func else "-"
            observaciones = eq.get("observaciones", "") or ""

            fila_texto = f"{eq.get('numero_serie','')} {eq.get('tipo_equipo','')} {eq.get('marca','')} {eq.get('modelo','')} {proveedor} {nombre_comercial} {custodio} {depto}".lower()
            if termino and termino.lower() not in fila_texto:
                continue

            datos.append([
                eq["id"],
                eq.get("numero_serie", ""),
                eq.get("tipo_equipo", ""),
                eq.get("marca", ""),
                eq.get("modelo", ""),
                custodio,
                depto,
                eq.get("estado", ""),
                proveedor,
                nombre_comercial,
                observaciones,
                "Mantener"
            ])
        if not datos:
            return [["-", "No se encontró coincidencia", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]
        return datos
    except Exception as e:
        return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def cargar_duplicados():
    try:
        res = supabase.table("equipos").select("id, numero_serie, tipo_equipo, marca, modelo, estado, observaciones, orden_id, ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, departamento)").execute()
        conteo = {}
        for eq in res.data:
            s_limpia = str(eq.get("numero_serie","")).upper().replace(" ", "")
            conteo[s_limpia] = conteo.get(s_limpia, 0) + 1

        datos = []
        for eq in res.data:
            s_limpia = str(eq.get("numero_serie","")).upper().replace(" ", "")
            if conteo[s_limpia] > 1:
                proveedor = ""
                nombre_comercial = ""
                ord_data = eq.get("ordenes_compra")
                if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
                if ord_data: 
                    proveedor = ord_data.get("razon_social_proveedor", "")
                    nombre_comercial = ord_data.get("nombre_comercial", "")
                
                func = eq.get("funcionarios")
                if isinstance(func, list) and func: func = func[0]
                elif not isinstance(func, dict): func = {}

                custodio = func.get("nombres_completos") if func else "Sin Asignar"
                depto = func.get("departamento") if func else "-"
                observaciones = eq.get("observaciones", "") or ""
                
                datos.append([
                    eq["id"],
                    eq.get("numero_serie", ""),
                    eq.get("tipo_equipo", ""),
                    eq.get("marca", ""),
                    eq.get("modelo", ""),
                    custodio,
                    depto,
                    eq.get("estado", ""),
                    proveedor,
                    nombre_comercial,
                    observaciones,
                    "Mantener"
                ])
                
        datos.sort(key=lambda x: str(x[1]).upper().replace(" ", ""))
        if not datos:
            return [["-", "No se encontraron series duplicadas", "-", "-", "-", "-", "-", "-", "-", "-", "-", "Mantener"]]
        return datos
    except Exception as e:
        return [[f"Error: {e}", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

def confirmar_edicion_masiva(df, request: gr.Request):
    usuario = request.username if request else "Sistema"
    if df is None or df.empty: return "⚠️ Tabla vacía."
    
    exitos_edit = 0
    exitos_del = 0
    errores = 0

    res_func = supabase.table("funcionarios").select("id, nombres_completos").execute()
    mapa_func_nombres = {str(f["nombres_completos"]).strip().upper(): f["id"] for f in res_func.data}

    for index, row in df.iterrows():
        try:
            id_bd_raw = str(row.iloc[0]).strip().replace(",", "")
            if id_bd_raw.endswith('.0'): id_bd_raw = id_bd_raw[:-2]
            if id_bd_raw == "-" or id_bd_raw.lower() == 'nan' or id_bd_raw == "": continue
            id_bd = id_bd_raw
            
            accion = str(row.iloc[11]).strip()
            if accion.lower() == "eliminar":
                supabase.table("equipos").delete().eq("id", id_bd).execute()
                exitos_del += 1
            else:
                serie = str(row.iloc[1]).strip()
                if serie.endswith('.0'): serie = serie[:-2]
                
                tipo = str(row.iloc[2]).strip()
                marca = str(row.iloc[3]).strip()
                modelo = str(row.iloc[4]).strip()
                custodio_editado = str(row.iloc[5]).strip()
                depto_editado = str(row.iloc[6]).strip()
                estado = str(row.iloc[7]).strip()
                proveedor = str(row.iloc[8]).strip()
                nombre_comercial = str(row.iloc[9]).strip()
                observaciones = str(row.iloc[10]).strip()

                datos_actualizar = {
                    "numero_serie": serie,
                    "tipo_equipo": tipo,
                    "marca": marca,
                    "modelo": modelo,
                    "estado": estado,
                    "observaciones": observaciones
                }

                if custodio_editado and custodio_editado.lower() not in ["", "nan", "-", "sin asignar"]:
                    func_id_nuevo = mapa_func_nombres.get(custodio_editado.upper())
                    if func_id_nuevo:
                        datos_actualizar["funcionario_id"] = func_id_nuevo
                        if depto_editado and depto_editado.lower() not in ["", "nan", "-"]:
                            supabase.table("funcionarios").update({"departamento": depto_editado}).eq("id", func_id_nuevo).execute()
                elif custodio_editado.lower() == "sin asignar":
                    datos_actualizar["funcionario_id"] = None

                res_eq = supabase.table("equipos").select("orden_id, funcionario_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").eq("id", id_bd).execute()
                if res_eq.data:
                    ord_data = res_eq.data[0].get("ordenes_compra")
                    if isinstance(ord_data, list) and ord_data: ord_data = ord_data[0]
                    elif not isinstance(ord_data, dict): ord_data = {}
                    
                    proveedor_actual = ord_data.get("razon_social_proveedor", "").strip() if ord_data else ""
                    nombre_comercial_actual = ord_data.get("nombre_comercial", "").strip() if ord_data else ""

                    if (proveedor and proveedor != proveedor_actual) or (nombre_comercial and nombre_comercial != nombre_comercial_actual):
                        identificador_unico = f"INDIV-{serie}"
                        res_indiv = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", identificador_unico).execute()
                        if res_indiv.data:
                            orden_nueva_id = res_indiv.data[0]["id"]
                            supabase.table("ordenes_compra").update({
                                "razon_social_proveedor": proveedor,
                                "nombre_comercial": nombre_comercial
                            }).eq("id", orden_nueva_id).execute()
                        else:
                            nueva_orden = {
                                "numero_proceso_sercop": identificador_unico,
                                "numero_orden_compra": "Independiente",
                                "razon_social_proveedor": proveedor,
                                "nombre_comercial": nombre_comercial,
                                "objeto_contratacion": "Separación individual",
                                "monto": 0.0,
                                "fecha_adquisicion": "2023-01-01"
                            }
                            res_insert = supabase.table("ordenes_compra").insert(nueva_orden).execute()
                            orden_nueva_id = res_insert.data[0]["id"]
                        datos_actualizar["orden_id"] = orden_nueva_id

                supabase.table("equipos").update(datos_actualizar).eq("id", id_bd).execute()
                exitos_edit += 1
        except Exception as e:
            print(f"Error procesando fila ID {row.iloc[0]}: {e}")
            errores += 1

    registrar_auditoria(usuario, f"Edición Masiva/Limpieza: {exitos_edit} editados, {exitos_del} eliminados.")
    return f"✅ Completado: {exitos_edit} actualizados, {exitos_del} eliminados. (Errores: {errores})"

def cargar_equipo_para_edicion(serie_combo):
    if not serie_combo: return "", "", "", "", "", "", "", "", None
    serie_limpia = serie_combo.split(" - ")[0].strip()
    try:
        res = supabase.table("equipos").select(
            "numero_serie, marca, modelo, estado, tipo_equipo, observaciones, orden_id, funcionario_id, "
            "ordenes_compra(razon_social_proveedor, nombre_comercial), funcionarios(nombres_completos, cedula)"
        ).ilike("numero_serie", f"{serie_limpia}%").execute()

        if res.data:
            item = res.data[0]
            serie_actual = item.get('numero_serie', '') or ''
            marca = item.get('marca', '') or ''
            modelo = item.get('modelo', '') or ''
            estado = item.get('estado', '')
            tipo = item.get('tipo_equipo', '')
            observaciones = item.get('observaciones', '') or ''
            ord_data = item.get('ordenes_compra')
            if isinstance(ord_data, list) and ord_data:
                ord_data = ord_data[0]
            proveedor = ord_data.get('razon_social_proveedor', '') if ord_data else ''
            nombre_comercial = ord_data.get('nombre_comercial', '') if ord_data else ''

            func = item.get('funcionarios')
            if isinstance(func, list) and func: func = func[0]
            elif not isinstance(func, dict): func = {}

            if item.get('funcionario_id') and func:
                custodio_actual = f"{func.get('nombres_completos','')} - {func.get('cedula','')}"
            else:
                custodio_actual = "Sin Asignar - BODEGA"

            return serie_actual, marca, modelo, estado, tipo, proveedor, nombre_comercial, observaciones, custodio_actual
        return "", "", "", "", "", "", "", "", None
    except Exception as e:
        print(f"Error cargando equipo para edición: {e}")
        return "", "", "", "", "", "", "", "", None

def guardar_edicion_equipo(serie_combo, serie_nueva, marca_nueva, modelo_nuevo, nuevo_estado, nuevo_tipo,
                            nuevo_proveedor, nuevo_nombre_comercial, nuevas_observaciones, custodio_nuevo,
                            request: gr.Request):
    usuario = request.username if request else "Sistema"
    if not serie_combo: return "⚠️ Selecciona un equipo."
    serie_limpia = serie_combo.split(" - ")[0].strip()
    serie_nueva_limpia = (serie_nueva or "").strip()
    nuevo_proveedor_limpio = (nuevo_proveedor or "").strip()
    nuevo_nombre_comercial_limpio = (nuevo_nombre_comercial or "").strip()

    if not serie_nueva_limpia:
        return "⚠️ El Número de Serie no puede quedar vacío."

    try:
        res_eq = supabase.table("equipos").select("id, orden_id, funcionario_id, ordenes_compra(razon_social_proveedor, nombre_comercial)").ilike("numero_serie", f"{serie_limpia}%").execute()
        if not res_eq.data:
            return "❌ Equipo no encontrado."

        equipo_id = res_eq.data[0]['id']
        orden_id_actual = res_eq.data[0].get("orden_id")
        ord_data = res_eq.data[0].get("ordenes_compra")
        
        if isinstance(ord_data, list) and ord_data:
            ord_data = ord_data[0]
        elif not isinstance(ord_data, dict):
            ord_data = {}
            
        proveedor_actual = (ord_data.get("razon_social_proveedor", "") or "").strip()
        nombre_comercial_actual = (ord_data.get("nombre_comercial", "") or "").strip()

        # Si el número de serie fue modificado, verificar que no choque con otro equipo existente
        if serie_nueva_limpia != serie_limpia:
            res_dup = supabase.table("equipos").select("id").eq("numero_serie", serie_nueva_limpia).execute()
            if res_dup.data:
                return f"❌ Error: Ya existe otro equipo registrado con la serie '{serie_nueva_limpia}'."

        datos_actualizar_equipo = {
            "numero_serie": serie_nueva_limpia,
            "estado": nuevo_estado, 
            "tipo_equipo": nuevo_tipo,
            "marca": marca_nueva,
            "modelo": modelo_nuevo,
            "observaciones": nuevas_observaciones
        }
        mensaje_extra = ""

        # Actualización de Custodio Asignado (funcionario)
        if custodio_nuevo:
            custodio_str = str(custodio_nuevo).strip()
            if custodio_str == "Sin Asignar - BODEGA":
                datos_actualizar_equipo["funcionario_id"] = None
            else:
                cedula_nueva = custodio_str.split(" - ")[-1].strip()
                res_func = supabase.table("funcionarios").select("id").eq("cedula", cedula_nueva).execute()
                if res_func.data:
                    datos_actualizar_equipo["funcionario_id"] = res_func.data[0]["id"]
                else:
                    mensaje_extra += " (⚠️ Custodio no encontrado, no se cambió la asignación)."

        if (nuevo_proveedor_limpio and nuevo_proveedor_limpio != proveedor_actual) or (nuevo_nombre_comercial_limpio and nuevo_nombre_comercial_limpio != nombre_comercial_actual):
            identificador_unico = f"INDIV-{serie_nueva_limpia}"
            res_indiv = supabase.table("ordenes_compra").select("id").eq("numero_proceso_sercop", identificador_unico).execute()
            
            if res_indiv.data:
                orden_nueva_id = res_indiv.data[0]["id"]
                supabase.table("ordenes_compra").update({
                    "razon_social_proveedor": nuevo_proveedor_limpio,
                    "nombre_comercial": nuevo_nombre_comercial_limpio
                }).eq("id", orden_nueva_id).execute()
            else:
                nueva_orden = {
                    "numero_proceso_sercop": identificador_unico,
                    "numero_orden_compra": "Independiente",
                    "razon_social_proveedor": nuevo_proveedor_limpio,
                    "nombre_comercial": nuevo_nombre_comercial_limpio,
                    "objeto_contratacion": "Separación individual de equipo",
                    "monto": 0.0,
                    "fecha_adquisicion": "2023-01-01"
                }
                res_insert = supabase.table("ordenes_compra").insert(nueva_orden).execute()
                orden_nueva_id = res_insert.data[0]["id"]

            datos_actualizar_equipo["orden_id"] = orden_nueva_id
            mensaje_extra += " (Proveedor/Nombre Comercial independizado solo para este equipo)."

        supabase.table("equipos").update(datos_actualizar_equipo).eq("id", equipo_id).execute()

        # Si se cambió la serie, actualizar el historial de mantenimientos para no dejarlos huérfanos
        if serie_nueva_limpia != serie_limpia:
            supabase.table("mantenimientos").update({"numero_serie": serie_nueva_limpia}).ilike("numero_serie", f"{serie_limpia}%").execute()
            mensaje_extra += " (Serie actualizada también en historiales)."

        registrar_auditoria(usuario, f"Editó datos completos del equipo {serie_limpia} (Serie/Marca/Modelo/Estado/Tipo/Custodio/Proveedor/Observaciones).")
        return f"✅ Guardado.{mensaje_extra}"

    except Exception as e:
        return f"❌ Error: {e}"

def generar_reporte_personalizado(orden, custodio, columnas, formato):
    datos_completos = cargar_datos_inventario("", as_styled=False)
    headers_completos = ["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"]
    
    if not columnas:
        return gr.update(visible=False, value=None), "⚠️ Selecciona al menos una columna para imprimir."
        
    datos_filtrados = []
    for fila in datos_completos:
        if fila[0] == "-" and fila[1] == "-" and fila[2] == "Sin resultados": continue
        
        orden_str = (str(fila[6]) + " " + str(fila[7])).lower()
        custodio_str = (str(fila[3]) + " " + str(fila[4])).lower()
        
        if orden and orden.lower() not in orden_str:
            continue
        if custodio and custodio.lower() not in custodio_str:
            continue
            
        fila_filtrada = [fila[headers_completos.index(col)] for col in columnas]
        datos_filtrados.append(fila_filtrada)
        
    if not datos_filtrados:
        return gr.update(visible=False, value=None), "⚠️ No se encontraron resultados con esos filtros."
        
    temp_dir = tempfile.gettempdir()
    fecha_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    try:
        if formato == "excel":
            ruta_excel = os.path.join(temp_dir, f"Reporte_Inventario_{fecha_str}.xlsx")
            df_reporte = pd.DataFrame(datos_filtrados, columns=columnas)
            df_reporte.to_excel(ruta_excel, index=False)
            return gr.update(value=ruta_excel, visible=True), "✅ Reporte Excel generado exitosamente."
            
        elif formato == "pdf":
            ruta_pdf = os.path.join(temp_dir, f"Reporte_Inventario_{fecha_str}.pdf")
            pdf = FPDF(orientation='L', unit='mm', format='A4')
            pdf.add_page()
            
            def limpiar(t):
                reemplazos = {'●': '-', '•': '-', '·': '-', '◦': '-', '→': '->', '←': '<-', '✓': 'OK', '✗': 'X', '\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"', '\u2013': '-', '\u2014': '--', '\u2026': '...'}
                t_str = str(t)
                for orig, rem in reemplazos.items(): t_str = t_str.replace(orig, rem)
                return t_str.encode('latin-1', 'replace').decode('latin-1')

            pdf.set_font("Arial", 'B', 14)
            pdf.set_fill_color(44, 62, 80)
            pdf.set_text_color(255, 255, 255)
            pdf.cell(0, 12, limpiar("REPORTE AVANZADO DE INVENTARIO Y ASIGNACIONES"), ln=True, align='C', fill=True)
            pdf.ln(5)
            
            pesos = {"Equipo": 1.5, "Marca/Modelo": 2.0, "Nº Serie": 1.5, "Custodio Asignado": 2.5, "Área/Dirección": 2.0, "Estado": 1.0, "Orden de Compra": 1.5, "Proceso SERCOP": 1.5, "Proveedor": 2.0, "Nombre Comercial": 1.5, "Observaciones": 2.0}
            peso_total = sum(pesos[c] for c in columnas)
            anchos = [(pesos[c] / peso_total) * 277 for c in columnas]
            
            pdf.set_font("Arial", 'B', 8)
            pdf.set_fill_color(220, 230, 241)
            pdf.set_text_color(0, 0, 0)
            for i, col in enumerate(columnas):
                pdf.cell(anchos[i], 8, limpiar(col), border=1, align='C', fill=True)
            pdf.ln()
            
            pdf.set_font("Arial", '', 7)
            for fila in datos_filtrados:
                for i, item in enumerate(fila):
                    texto = limpiar(item)
                    max_chars = int(anchos[i] / 1.5)
                    if len(texto) > max_chars: texto = texto[:max_chars-2] + ".."
                    pdf.cell(anchos[i], 6, texto, border=1)
                pdf.ln()
                
            pdf.output(ruta_pdf)
            return gr.update(value=ruta_pdf, visible=True), "✅ Reporte PDF generado exitosamente."
    except Exception as e:
        return gr.update(visible=False, value=None), f"❌ Error al generar reporte: {e}"

def exportar_pdf_inv(orden, custodio, columnas): return generar_reporte_personalizado(orden, custodio, columnas, "pdf")
def exportar_excel_inv(orden, custodio, columnas): return generar_reporte_personalizado(orden, custodio, columnas, "excel")

# --- 4. INTERFAZ APO (UI CONSTRUCCIÓN) ---
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
                            
                            tabla_preview_acta = gr.Dataframe(
                                headers=["Tipo", "Marca", "Modelo", "Serie", "Proveedor", "Nombre Comercial", "Orden de Compra", "Proceso SERCOP", "Observaciones"],
                                column_widths=["100px", "100px", "100px", "120px", "150px", "150px", "130px", "150px", "250px"],
                                interactive=True,
                                wrap=True,
                                label="Vista Previa (Puedes editar los datos directamente en esta tabla antes de guardar)"
                            )
                            datos_acta_state = gr.State({})
                            
                            btn_procesar_acta = gr.Button("2. Confirmar y Guardar en Base de Datos", variant="primary")
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
                            gr.Markdown("**🔍 Ver o Editar Funcionario Existente:**")
                            buscar_func_combo = gr.Dropdown(label="Buscar Funcionario Registrado", choices=[], interactive=True)
                            gr.Markdown("---")
                            cedula_in = gr.Textbox(label="Código del GAD (Identificador Único)")
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
                            btn_analizar_excel = gr.Button("1. Analizar y Previsualizar Datos", variant="secondary")
                            
                            tabla_preview = gr.Dataframe(
                                headers=["Nº Serie Exacta", "Código del GAD Final", "Nombres", "Cargo", "Departamento", "Validación"], 
                                interactive=True, 
                                wrap=True,
                                label="Vista Previa (Puedes editar y corregir las celdas directamente aquí antes de confirmar)"
                            )
                            
                            btn_confirmar_masivo = gr.Button("2. Confirmar y Asignar Equipos", variant="primary")
                            mensaje_masivo = gr.Textbox(label="Resultado", interactive=False)

                    with gr.Group():
                        gr.Markdown("#### ⚙️ Mantenimiento del Inventario")
                        with gr.Accordion("6. Edición Masiva y Limpieza de Inventario", open=False):
                            gr.Markdown("**A. Liberar Equipo a Estado 'Sin Asignar':**")
                            with gr.Row():
                                serie_liberar = gr.Dropdown(label="Selecciona Equipo Ocupado", choices=[], interactive=True)
                                btn_liberar = gr.Button("🔓 Liberar Equipo", variant="stop")
                            mensaje_liberar = gr.Textbox(show_label=False, interactive=False)

                            gr.Markdown("---")
                            gr.Markdown("**A.1 Dar de Baja / Eliminar Equipo del Inventario (solo si está LIBRE):**")
                            gr.Markdown("*(⚠️ Esta acción marcará el equipo en ROJO (De Baja) y guardará tu motivo en las observaciones del equipo, manteniendo el registro en el historial.)*")
                            with gr.Row():
                                serie_eliminar_equipo = gr.Dropdown(label="Selecciona Equipo Libre a Eliminar", choices=[], interactive=True)
                                motivo_eliminar_equipo = gr.Textbox(label="Motivo de eliminación (Obligatorio)", placeholder="Ej: Equipo obsoleto, perdido, devuelto...")
                                btn_eliminar_equipo = gr.Button("🗑️ Eliminar / Dar de Baja", variant="stop")
                            mensaje_eliminar_equipo = gr.Textbox(show_label=False, interactive=False)
                            
                            gr.Markdown("---")
                            gr.Markdown("**B. Búsqueda, Edición en Tabla y Eliminación de Duplicados:**")
                            gr.Markdown("Puedes editar *Nº Serie, Tipo, Marca, Modelo, Estado, Proveedor, Nombre Comercial y Observaciones* directamente en la tabla. Para borrar un registro repetido, escribe **Eliminar** en la columna *Acción*.")
                            with gr.Row():
                                txt_buscar_edicion = gr.Textbox(label="Buscar equipo específico para editar", placeholder="Ej: Serie o Marca")
                                btn_buscar_edicion = gr.Button("🔍 Buscar", variant="secondary")
                                btn_buscar_duplicados = gr.Button("⚠️ Buscar Duplicados", variant="stop")
                                
                            tabla_edicion = gr.Dataframe(
                                headers=["ID BD", "Nº Serie", "Tipo", "Marca", "Modelo", "Custodio Asignado", "Área/Dirección", "Estado", "Proveedor", "Nombre Comercial", "Observaciones", "Acción"],
                                column_widths=["80px", "130px", "100px", "100px", "120px", "150px", "150px", "100px", "150px", "150px", "200px", "100px"],
                                interactive=True,
                                wrap=True
                            )
                            btn_guardar_edicion_tabla = gr.Button("💾 Confirmar Cambios y Eliminaciones", variant="primary")
                            mensaje_edicion_tabla = gr.Textbox(label="Estado de Edición", interactive=False)
                            
                            gr.Markdown("---")
                            gr.Markdown("**C. Edición Rápida (Formulario) — Edita TODOS los datos del equipo:**")
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
                            caja_busqueda = gr.Textbox(label="Buscar por: Serie, Código del GAD, Nombre, Orden o Proveedor", placeholder="Ej. CE-2023... o Toshiba", scale=4)
                            btn_buscar = gr.Button("🔎 Buscar", variant="primary", scale=1)
                            btn_sincronizar = gr.Button("🔄 Mostrar Todos", scale=1)
                        
                        gr.Markdown("*(💡 Tip: Haz clic en cualquier equipo de la tabla para cargarlo en el Acordeón 6 de Edición)*")
                        tabla_inventario = gr.Dataframe(
                            headers=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"], 
                            column_widths=["120px", "150px", "130px", "150px", "150px", "100px", "130px", "150px", "150px", "150px", "250px"],
                            interactive=False, 
                            wrap=True
                        )

                    with gr.Group():
                        with gr.Accordion("🖨️ Generar Reporte Personalizado", open=False):
                            gr.Markdown("Selecciona los filtros y las columnas que deseas imprimir/exportar en tu reporte de inventario.")
                            with gr.Row():
                                rep_orden = gr.Textbox(label="Filtrar por Orden de Compra o Proceso", placeholder="Ej: CE-2023... (Vacío para listar todos)")
                                rep_custodio = gr.Textbox(label="Filtrar por Custodio o Área", placeholder="Ej: Juan Perez o IT (Vacío para listar todos)")
                            
                            rep_columnas = gr.CheckboxGroup(
                                choices=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Proceso SERCOP", "Proveedor", "Nombre Comercial", "Observaciones"],
                                value=["Equipo", "Marca/Modelo", "Nº Serie", "Custodio Asignado", "Área/Dirección", "Estado", "Orden de Compra", "Nombre Comercial"],
                                label="Selecciona las Columnas a Imprimir"
                            )
                            with gr.Row():
                                btn_rep_pdf = gr.Button("📄 Descargar PDF", variant="primary")
                                btn_rep_excel = gr.Button("📊 Descargar Excel", variant="secondary")
                                
                            rep_mensaje = gr.Textbox(show_label=False, interactive=False)
                            rep_archivo = gr.File(label="Archivo Reporte Generado", visible=False)

        with gr.TabItem("3. Gestión de Mantenimientos"):
            gr.Markdown("### 🛠️ Registro Técnico y Generación de Reportes de Mantenimiento")
            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("#### 📝 Detalles de la Intervención")
                    
                    custodio_mantenimiento = gr.Dropdown(
                        label="1. Funcionario / Ubicación",
                        choices=[],
                        interactive=True
                    )
                    
                    serie_mantenimiento = gr.Dropdown(
                        label="2. Equipo(s) a intervenir",
                        choices=[],
                        interactive=True,
                        multiselect=True
                    )
                    
                    with gr.Row():
                        fecha_mantenimiento = gr.Textbox(label="3. Fecha", elem_classes="fecha-calendario", lines=1, max_lines=1)
                        proximo_mantenimiento = gr.Textbox(label="4. Próximo Mantenimiento (Opcional)", elem_classes="fecha-calendario", lines=1, max_lines=1)

                    tipo_mantenimiento = gr.Dropdown(label="5. Tipo de Intervención", choices=["Preventivo", "Correctivo", "Revisión de Garantía", "De Baja", "Diagnóstico"])
                    tecnico_proveedor = gr.Dropdown(label="6. Empresa / Técnico Asignado", choices=[], interactive=True, allow_custom_value=True)

                    gr.Markdown("---")
                    desc_checks = gr.Dropdown(
                        label="7. Trabajos Realizados",
                        choices=["Limpieza Interna (Sopleteado/Brocha)", "Reemplazo de Componentes/Periféricos",
                                 "Actualización de Software/Drivers", "Formateo y Reinstalación de SO",
                                 "Revisión de Fuente/Voltajes", "Limpieza Lógica de Virus"],
                        multiselect=True, allow_custom_value=True
                    )
                    desc_mantenimiento = gr.Textbox(label="Detalles adicionales / Observaciones", lines=2)
                    costo_mantenimiento = gr.Number(label="8. Costo Asociado ($)", value=0.0)

                    gr.Markdown("---")
                    fotos_mantenimiento = gr.File(
                        label="9. Evidencia Fotográfica",
                        file_count="multiple",
                        file_types=["image"],
                        type="filepath"
                    )

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
                        btn_eliminar_mant = gr.Button("🗑️ Eliminar Registro Seleccionado", variant="stop")
                    archivo_reporte_descarga = gr.File(label="📄 Reporte Descargado", visible=False)
                    msg_eliminar = gr.Textbox(show_label=False, interactive=False)

                with gr.Column(scale=2):
                    gr.Markdown("#### 📋 Historial Técnico Global / Por Funcionario")
                    with gr.Row():
                        btn_ver_todo_historial = gr.Button("🔄 Cargar TODO el Historial del Sistema", variant="secondary")
                    
                    gr.Markdown("*(Se actualiza al seleccionar un Funcionario, o presiona el botón arriba para ver todos los registros del GAD)*")
                    
                    tabla_mantenimientos = gr.Dataframe(
                        headers=["Fecha", "Tipo", "Responsable", "Equipo(s) Intervenido(s)", "Descripción", "Costo", "Próxima Revisión"],
                        interactive=False, wrap=True
                    )

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
                gr.Markdown("El sistema descarga y empaqueta automáticamente toda la base de datos (JSON) en la carpeta `respaldos` cada 24 horas. También puedes generar un respaldo manual en cualquier momento presionando este botón:")
                with gr.Row():
                    btn_respaldo = gr.Button("⬇️ Generar y Descargar Respaldo Manual", variant="primary")
                with gr.Row():
                    msg_respaldo = gr.Textbox(show_label=False, interactive=False)
                    archivo_respaldo = gr.File(label="Archivo de Respaldo Generado (JSON)", visible=False)

    # =========================================================================================
    # ======================== SECCIÓN ÚNICA DE EVENTOS (PREVIENE ERRORES) ====================
    # =========================================================================================

    # Eventos Pestaña 1
    btn_analizar.click(fn=analizar_fase_precontractual, inputs=[archivos_input, referencias_input, enlaces_input], outputs=reporte_output)

    # Eventos Pestaña 2 (Inventario y Asignaciones)
    btn_logout.click(fn=None, inputs=None, outputs=None, js="() => { window.location.href = '/logout'; }")
    btn_analizar_acta.click(fn=analizar_acta_pdf, inputs=[acta_input], outputs=[tabla_preview_acta, datos_acta_state, mensaje_acta])
    btn_procesar_acta.click(fn=procesar_acta_recepcion, inputs=[tabla_preview_acta, datos_acta_state], outputs=[mensaje_acta, tabla_inventario, serie_asignar])
    btn_guardar_manual.click(fn=ingresar_equipo_manual, inputs=[num_proceso_man, num_orden_man, proveedor_man, nombre_comercial_man, objeto_man, monto_man, tipo_man, marca_man, modelo_man, serie_man, observaciones_man], outputs=[mensaje_manual, tabla_inventario, serie_asignar])
    
    tabla_funcionarios.select(fn=seleccionar_funcionario_tabla, inputs=[tabla_funcionarios], outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo])
    buscar_func_combo.change(fn=cargar_datos_funcionario_form, inputs=[buscar_func_combo], outputs=[cedula_in, nombres_in, cargo_in, depto_in])
    btn_guardar_func.click(fn=registrar_y_actualizar, inputs=[cedula_in, nombres_in, cargo_in, depto_in], outputs=[mensaje_func, custodio_asignar, tabla_funcionarios, buscar_func_combo])
    btn_limpiar_func.click(fn=lambda: ("", "", "", "", gr.update(value=None)), inputs=[], outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo])
    
    btn_eliminar_func.click(
        fn=eliminar_funcionario, 
        inputs=[cedula_in], 
        outputs=[mensaje_func, custodio_asignar, tabla_funcionarios, buscar_func_combo]
    ).then(
        fn=lambda: ("", "", "", "", gr.update(value=None)), 
        inputs=[], 
        outputs=[cedula_in, nombres_in, cargo_in, depto_in, buscar_func_combo]
    )

    custodio_asignar.change(fn=advertencia_asignacion, inputs=[custodio_asignar], outputs=[alerta_asignacion])
    btn_asignar.click(fn=asignar_custodio_equipo, inputs=[serie_asignar, custodio_asignar, garantia_input], outputs=[mensaje_asignar, tabla_inventario, serie_asignar, custodio_asignar, serie_liberar])
    
    btn_analizar_excel.click(fn=analizar_excel_masivo, inputs=[excel_input], outputs=[mensaje_masivo, tabla_preview])
    btn_confirmar_masivo.click(
        fn=confirmar_asignacion_masiva, inputs=[tabla_preview], outputs=[mensaje_masivo]
    ).then(
        fn=cargar_todo_ui, inputs=[],
        outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar,
                 serie_editar, tabla_funcionarios, buscar_func_combo,
                 buscar_usuario_combo, custodio_mantenimiento]
    )

    tabla_inventario.select(fn=seleccionar_inventario_tabla, inputs=[tabla_inventario], outputs=[serie_editar, estado_editar, tipo_editar, serie_liberar])
    btn_buscar.click(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    caja_busqueda.submit(fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario])
    btn_sincronizar.click(
        fn=cargar_todo_ui, inputs=[], 
        outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar, serie_editar, tabla_funcionarios, buscar_func_combo, buscar_usuario_combo, custodio_mantenimiento]
    )
    
    btn_liberar.click(fn=liberar_equipo, inputs=[serie_liberar], outputs=[mensaje_liberar, tabla_inventario, serie_asignar, serie_liberar])

    # Evento: Dar de Baja equipo (Borrado Suave / Lógico)
    btn_eliminar_equipo.click(
        fn=eliminar_equipo_inventario,
        inputs=[serie_eliminar_equipo, motivo_eliminar_equipo],
        outputs=[mensaje_eliminar_equipo, tabla_inventario, serie_asignar, serie_liberar, serie_eliminar_equipo, tabla_auditoria, motivo_eliminar_equipo]
    ).then(
        fn=obtener_series_disponibles, inputs=[], outputs=[serie_eliminar_equipo]
    )

    btn_buscar_edicion.click(fn=cargar_tabla_edicion, inputs=[txt_buscar_edicion], outputs=[tabla_edicion])
    btn_buscar_duplicados.click(fn=cargar_duplicados, inputs=[], outputs=[tabla_edicion])
    btn_guardar_edicion_tabla.click(fn=confirmar_edicion_masiva, inputs=[tabla_edicion], outputs=[mensaje_edicion_tabla]).then(
        fn=realizar_busqueda, inputs=[caja_busqueda], outputs=[tabla_inventario]
    )
    
    serie_editar.change(
        fn=cargar_equipo_para_edicion,
        inputs=[serie_editar],
        outputs=[serie_nueva_editar, marca_editar, modelo_editar, estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar, custodio_editar]
    )
    btn_guardar_edicion.click(
        fn=guardar_edicion_equipo,
        inputs=[serie_editar, serie_nueva_editar, marca_editar, modelo_editar, estado_editar, tipo_editar, proveedor_editar, nombre_comercial_editar, observaciones_editar, custodio_editar],
        outputs=[mensaje_edicion]
    ).then(
        fn=cargar_todo_ui, inputs=[],
        outputs=[tabla_inventario, serie_asignar, custodio_asignar, serie_liberar,
                 serie_editar, tabla_funcionarios, buscar_func_combo,
                 buscar_usuario_combo, custodio_mantenimiento]
    ).then(
        fn=obtener_funcionarios_mantenimiento, inputs=[], outputs=[custodio_editar]
    )

    btn_rep_pdf.click(fn=exportar_pdf_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])
    btn_rep_excel.click(fn=exportar_excel_inv, inputs=[rep_orden, rep_custodio, rep_columnas], outputs=[rep_archivo, rep_mensaje])

    # Eventos Pestaña 3 (Mantenimientos)
    custodio_mantenimiento.change(
        fn=cargar_equipos_de_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[serie_mantenimiento, nombre_funcionario_firma]
    ).then(
        fn=cargar_historial_por_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[tabla_mantenimientos]
    ).then(
        fn=obtener_mantenimientos_lista_por_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[registro_eliminar]
    )

    serie_mantenimiento.change(
        fn=auto_completar_mantenimiento,
        inputs=[serie_mantenimiento],
        outputs=[tecnico_proveedor, tabla_mantenimientos]
    )

    btn_guardar_mantenimiento.click(
        fn=registrar_y_generar_acta,
        inputs=[custodio_mantenimiento, serie_mantenimiento, fecha_mantenimiento, tipo_mantenimiento,
                desc_checks, desc_mantenimiento, tecnico_proveedor,
                costo_mantenimiento, proximo_mantenimiento, fotos_mantenimiento,
                nombre_admin_firma, nombre_tecnico_firma, nombre_funcionario_firma],
        outputs=[msg_mantenimiento, tabla_mantenimientos, archivo_acta_descarga]
    ).then(
        fn=obtener_mantenimientos_lista_por_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[registro_eliminar]
    )

    btn_descargar_reporte.click(
        fn=descargar_reporte_mantenimiento,
        inputs=[registro_eliminar],
        outputs=[archivo_reporte_descarga, msg_eliminar]
    )

    btn_eliminar_mant.click(
        fn=eliminar_mantenimiento,
        inputs=[registro_eliminar],
        outputs=[msg_eliminar]
    ).then(
        fn=obtener_mantenimientos_lista_por_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[registro_eliminar]
    ).then(
        fn=cargar_historial_por_funcionario,
        inputs=[custodio_mantenimiento],
        outputs=[tabla_mantenimientos]
    )

    btn_ver_todo_historial.click(
        fn=cargar_historial_completo,
        inputs=[],
        outputs=[tabla_mantenimientos]
    )

    # Eventos Pestaña 4 y 5 (Auditoría y Seguridad)
    btn_refrescar_auditoria.click(fn=cargar_datos_auditoria, inputs=[], outputs=tabla_auditoria)
    btn_respaldo.click(fn=generar_respaldo_manual, inputs=[], outputs=[archivo_respaldo, msg_respaldo])
    tabla_usuarios.select(fn=seleccionar_usuario_tabla, inputs=[tabla_usuarios], outputs=[usuario_db_in, rol_db_in, buscar_usuario_combo])
    buscar_usuario_combo.change(fn=cargar_datos_usuario_form, inputs=[buscar_usuario_combo], outputs=[usuario_db_in, rol_db_in])
    btn_limpiar_usr.click(fn=lambda: ("", "admin", "", gr.update(value=None)), inputs=[], outputs=[usuario_db_in, rol_db_in, clave_db_in, buscar_usuario_combo])
    btn_refrescar_usuarios.click(fn=cargar_usuarios_sistema, inputs=[], outputs=[tabla_usuarios])
    btn_reset_clave.click(fn=gestionar_usuario_admin, inputs=[usuario_db_in, clave_db_in, rol_db_in], outputs=[mensaje_clave, tabla_usuarios, buscar_usuario_combo])

    # Script JS para calendarios
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
            buscar_usuario_combo, custodio_mantenimiento, panel_seguridad, panel_denegado,
            custodio_editar
        ],
        js=script_calendario
    ).then(
        fn=obtener_series_disponibles, inputs=[], outputs=[serie_eliminar_equipo]
    )

# --- ADAPTACIÓN PARA RENDER Y USO LOCAL ---
import fastapi
app = fastapi.FastAPI()
app = gr.mount_gradio_app(app, erp_interfaz, path="/", auth=verificar_credenciales)

if __name__ == "__main__":
    import uvicorn
    # En tu PC local correrá en http://localhost:10000
    uvicorn.run(app, host="0.0.0.0", port=10000)
