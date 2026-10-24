import streamlit as st
import pandas as pd
import pdfplumber
import json
import re
from PIL import Image
from supabase import create_client, Client

try:
    import zxingcpp
    ZXING_AVAILABLE = True
except ImportError:
    ZXING_AVAILABLE = False

try:
    from pyzbar.pyzbar import decode as pyzbar_decode
    PYZBAR_AVAILABLE = True
except Exception:
    PYZBAR_AVAILABLE = False

st.set_page_config(page_title="Sistema de Chequeo de Productos", page_icon="📦", layout="wide")

st.markdown("""
<style>
    /* Estilos de las pestañas principales */
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #f1f5f9;
        padding: 6px;
        border-radius: 10px;
        border: 1px solid #e2e8f0;
    }
    .stTabs [data-baseweb="tab"] {
        font-size: 15px;
        font-weight: 700;
        color: #64748b;
        padding: 10px 22px;
        border-radius: 8px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ffffff !important;
        color: #2563eb !important;
        box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }

    /* Estilos del cuadro de búsqueda y formulario */
    div[data-testid="stForm"] {
        background: #f8fafc;
        border: 2px solid #2563eb;
        border-radius: 12px;
        padding: 18px 20px 14px 20px;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.12);
    }
    div[data-testid="stTextInput"] input {
        font-size: 18px !important;
        font-weight: 600 !important;
        color: #0f172a !important;
        background-color: #ffffff !important;
        border: 1.5px solid #94a3b8 !important;
        border-radius: 8px !important;
        padding: 10px 14px !important;
    }
    div[data-testid="stTextInput"] input:focus {
        border-color: #2563eb !important;
        box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.25) !important;
    }
    div[data-testid="stTextInput"] label p {
        font-size: 13px !important;
        font-weight: 700 !important;
        color: #1e293b !important;
        letter-spacing: 0.3px;
        text-transform: uppercase;
    }
</style>
""", unsafe_allow_html=True)

# --- CONEXIÓN CON SUPABASE ---
@st.cache_resource
def get_supabase_client() -> Client:
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_KEY"]
        return create_client(url, key)
    except Exception as e:
        st.error("⚠️ Faltan las credenciales de Supabase en Secrets (SUPABASE_URL y SUPABASE_KEY).")
        st.stop()

supabase = get_supabase_client()

# --- FUNCIONES DE BASE DE DATOS (NUBE SUPABASE) ---
def obtener_facturas():
    try:
        res = supabase.table("facturas").select("nombre").order("id", desc=True).execute()
        return [row["nombre"] for row in res.data]
    except Exception:
        return []

def obtener_info_factura(nombre_factura):
    try:
        res = supabase.table("facturas").select("columnas_json, col_barra, col_cant").eq("nombre", nombre_factura).execute()
        if res.data:
            row = res.data[0]
            cols = row["columnas_json"]
            if isinstance(cols, str):
                cols = json.loads(cols)
            return cols, row.get("col_barra", ""), row.get("col_cant", "")
    except Exception:
        pass
    return [], "", ""

def cargar_productos_factura(nombre_factura):
    try:
        res = supabase.table("productos_dinamicos").select("id, datos_json, codigo_barra, cant_total, cant_registrada, estado").eq("factura", nombre_factura).order("id").execute()
        lista = []
        for r in res.data:
            item = r["datos_json"]
            if isinstance(item, str):
                item = json.loads(item)
            item['_id'] = r["id"]
            item['_codigo_barra'] = r.get("codigo_barra", "")
            item['_cant_total'] = r.get("cant_total", 0)
            item['CANT. REGISTRADA'] = r.get("cant_registrada", 0)
            item['ESTADO'] = r.get("estado", "Pendiente")
            lista.append(item)
        return pd.DataFrame(lista)
    except Exception:
        return pd.DataFrame()

def actualizar_producto_dinamico(prod_id, cant_sumar=None, completar=False, resetear=False):
    try:
        res = supabase.table("productos_dinamicos").select("cant_total, cant_registrada").eq("id", prod_id).execute()
        if res.data:
            row = res.data[0]
            cant_total = row["cant_total"]
            cant_reg = row["cant_registrada"]
            
            if resetear:
                nueva_cant = 0
                nuevo_estado = "Pendiente"
            elif completar:
                nueva_cant = cant_total
                nuevo_estado = "Completado"
            else:
                nueva_cant = cant_reg + (cant_sumar if cant_sumar is not None else 1)
                nuevo_estado = "Completado" if nueva_cant >= cant_total else ("En Proceso" if nueva_cant > 0 else "Pendiente")
            
            supabase.table("productos_dinamicos").update({
                "cant_registrada": nueva_cant,
                "estado": nuevo_estado
            }).eq("id", prod_id).execute()
    except Exception as e:
        st.error(f"Error al actualizar: {e}")

def eliminar_factura_completa(nombre_factura):
    try:
        supabase.table("productos_dinamicos").delete().eq("factura", nombre_factura).execute()
        supabase.table("facturas").delete().eq("nombre", nombre_factura).execute()
        return True
    except Exception as e:
        st.error(f"Error al eliminar la factura: {e}")
        return False

# --- FUNCIÓN DECODIFICADORA DE CÓDIGOS DE BARRA DESDE CÁMARA ---
def decodificar_codigo_imagen(imagen_file):
    try:
        img = Image.open(imagen_file)
        if ZXING_AVAILABLE:
            resultados = zxingcpp.read_barcodes(img)
            if resultados:
                return resultados[0].text.strip()
        if PYZBAR_AVAILABLE:
            resultados = pyzbar_decode(img)
            if resultados:
                return resultados[0].data.decode("utf-8").strip()
    except Exception as e:
        st.error(f"Error al procesar la imagen de la cámara: {e}")
    return None

# --- DICCIONARIO Y SEGMENTADOR DE PALABRAS PEGADAS ---
PALABRAS_CLAVE = {
    "marcador", "marcadores", "permanente", "tablero", "doble", "punta", "fina", "gruesa", "redonda",
    "biselada", "libro", "para", "colorear", "pag", "borrador", "borradores", "dinosaurios", "rex",
    "pcs", "pc", "pompones", "color", "colores", "pastel", "figuras", "foamy", "fomi", "adhesivas",
    "adhesivo", "adhesiva", "mariposas", "letras", "papel", "foto", "hjs", "tamaño", "carta", "oficio",
    "etiqueta", "precio", "blanca", "blanco", "negro", "azul", "rojo", "verde", "amarillo", "morado",
    "celeste", "naranja", "cafe", "rosa", "dorado", "plateado", "glitter", "lapiz", "lapices", "infinito",
    "endless", "capibara", "artesco", "pointer", "multimax", "boligrafo", "boligrafos", "trimax",
    "engrapadora", "grapas", "mini", "almohadilla", "dactilar", "pegamento", "barra", "triangular",
    "sticky", "magic", "pintura", "acrilica", "matte", "silicon", "silicona", "liquido", "liquida",
    "tijera", "escolar", "regla", "plastico", "metalica", "cuaderno", "cuadernos", "espiral", "cosido",
    "hojas", "carpeta", "folder", "sobre", "manila", "cinta", "cintas", "tape", "masking", "embalaje",
    "resma", "bond", "lustrillo", "crepe", "china", "seda", "celofan", "carbon", "milimetrado", "cartulina",
    "carton", "resaltador", "resaltadores", "plumon", "plumones", "crayolas", "tempera", "temperas",
    "acuarela", "acuarelas", "pincel", "pinceles", "paleta", "plastilina", "masa", "stickers", "globos",
    "globo", "latex", "metalico", "bomba", "de", "del", "la", "el", "los", "las", "un", "una", "en", "con", "sin", "por"
}
PALABRAS_ORDENADAS = sorted(list(PALABRAS_CLAVE), key=len, reverse=True)

def reparar_espacios_texto(texto):
    if not texto:
        return ""
    t = str(texto).strip().replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t)

    t = re.sub(r"([a-zñáéíóú])([A-ZÑÁÉÍÓÚ])", r"\1 \2", t)
    t = re.sub(r"(\d+)([a-zA-ZñáéíóúÑÁÉÍÓÚ])", r"\1 \2", t)
    t = re.sub(r"([a-zA-ZñáéíóúÑÁÉÍÓÚ])(\d+)", r"\1 \2", t)
    t = re.sub(r"([a-zA-Z0-9])(/)", r"\1 /", t)
    t = re.sub(r"(/)([a-zA-Z0-9])", r"/ \2", t)

    tokens = t.split()
    tokens_corregidos = []
    for tok in tokens:
        if len(tok) >= 7 and not tok.startswith("http") and not ("/" in tok or "-" in tok and tok.count("-") > 1):
            tok_low = tok.lower()
            idx = 0
            subpartes = []
            while idx < len(tok_low):
                matched = False
                for p in PALABRAS_ORDENADAS:
                    if tok_low[idx:].startswith(p):
                        subpartes.append(tok[idx:idx+len(p)])
                        idx += len(p)
                        matched = True
                        break
                if not matched:
                    if subpartes:
                        subpartes[-1] += tok[idx]
                    else:
                        subpartes.append(tok[idx])
                    idx += 1
            tokens_corregidos.append(" ".join(subpartes))
        else:
            tokens_corregidos.append(tok)

    return re.sub(r"\s+", " ", " ".join(tokens_corregidos)).strip()

def extraer_todas_las_tablas(uploaded_file):
    filas_totales = []
    table_settings = {
        "text_x_tolerance": 1,
        "text_y_tolerance": 2,
        "snap_y_tolerance": 4,
        "intersection_x_tolerance": 8
    }
    
    with pdfplumber.open(uploaded_file) as pdf:
        for page in pdf.pages:
            tablas = page.extract_tables(table_settings=table_settings)
            if not tablas or len(tablas) == 0:
                tablas = page.extract_tables()
            
            for t in tablas:
                if not t:
                    continue
                for row in t:
                    clean_row = [reparar_espacios_texto(col) for col in row]
                    if any(clean_row):
                        filas_totales.append(clean_row)

    if not filas_totales:
        return None

    max_c = max(len(r) for r in filas_totales)
    normalizadas = [r + [""] * (max_c - len(r)) for r in filas_totales]
    return pd.DataFrame(normalizadas)

def auto_detectar_fila_encabezados(df):
    keywords = ["codigo", "código", "sku", "descripcion", "descripción", "articulo", "artículo", "cantidad", "cant", "precio", "total", "barra", "ean"]
    mejor_fila = 1
    max_coincidencias = 0
    limite = min(len(df), 20)
    for i in range(limite):
        fila_texto = " ".join([str(val).lower() for val in df.iloc[i]])
        if any(term in fila_texto for term in ["total productos", "total unidades", "cotizaciones", "regla de calculo"]):
            continue
        coincidencias = sum(1 for kw in keywords if kw in fila_texto)
        if coincidencias > max_coincidencias:
            max_coincidencias = coincidencias
            mejor_fila = i + 1
    return mejor_fila

# --- CABECERA PRINCIPAL MODERNA ---
st.markdown("""
<div style="background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); padding: 22px 28px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 4px 14px rgba(0,0,0,0.12); display: flex; align-items: center; justify-content: space-between;">
    <div>
        <div style="display: flex; align-items: center; gap: 12px;">
            <span style="font-size: 32px;">📦</span>
            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 700; letter-spacing: -0.5px;">Sistema de Chequeo de Productos</h1>
        </div>
        <p style="color: #94a3b8; margin: 4px 0 0 44px; font-size: 13px; font-weight: 400;">Control operativo de inventario, pistoleo y verificación de facturas en tiempo real</p>
    </div>
</div>
""", unsafe_allow_html=True)

with st.sidebar:
    st.header("📂 Factura en Trabajo")
    facturas_lista = obtener_facturas()
    factura_activa = st.selectbox("Seleccionar Factura Activa:", facturas_lista if facturas_lista else ["Sin facturas"])
    if st.button("🔄 Refrescar datos", use_container_width=True):
        st.rerun()
    st.divider()

    if factura_activa and factura_activa != "Sin facturas":
        with st.expander("🗑️ Eliminar Factura Actual"):
            st.warning(f"¿Deseas eliminar definitivamente '{factura_activa}' y todos sus productos?")
            confirmar = st.checkbox("Confirmar eliminación", key=f"conf_del_{factura_activa}")
            if st.button("Eliminar Factura de la Base de Datos", type="primary", disabled=not confirmar, use_container_width=True):
                if eliminar_factura_completa(factura_activa):
                    st.success(f"Factura '{factura_activa}' eliminada.")
                    st.rerun()

# --- PESTAÑAS PRINCIPALES DEL SISTEMA ---
tab_operativa, tab_subida = st.tabs([
    "🔍 Chequeo y Pistoleo Diario",
    "📥 Cargar / Configurar Nueva Factura"
])

# ==============================================================================
# PESTAÑA 1: ÁREA OPERATIVA DE CHEQUEO Y PISTOLEO (100% LIMPIA)
# ==============================================================================
with tab_operativa:
    if factura_activa and factura_activa != "Sin facturas":
        columnas_orig, col_barra_cfg, col_cant_cfg = obtener_info_factura(factura_activa)
        df_act = cargar_productos_factura(factura_activa)

        if not df_act.empty:
            tot_unidades = int(df_act['_cant_total'].sum())
            tot_registradas = int(df_act['CANT. REGISTRADA'].sum())
            tot_items = len(df_act)
            items_listos = len(df_act[df_act['ESTADO'] == 'Completado'])
            progreso = (tot_registradas / tot_unidades) if tot_unidades > 0 else 0.0

            # --- TARJETAS KPI ---
            c_card1, c_card2, c_card3, c_card4 = st.columns([2.2, 1.2, 1.2, 1.2])

            with c_card1:
                st.markdown(f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 5px solid #2563eb; border-radius: 10px; padding: 14px 18px; min-height: 98px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">Factura Activa</div>
                    <div style="font-size: 17px; font-weight: 700; color: #0f172a; word-break: break-word; line-height: 1.35;" title="{factura_activa}">{factura_activa}</div>
                </div>
                """, unsafe_allow_html=True)

            with c_card2:
                st.markdown(f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 5px solid #0891b2; border-radius: 10px; padding: 14px 18px; min-height: 98px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">Ítems Listos</div>
                    <div style="font-size: 26px; font-weight: 800; color: #0891b2; line-height: 1.1;">{items_listos} <span style="font-size: 15px; font-weight: 600; color: #94a3b8;">/ {tot_items}</span></div>
                </div>
                """, unsafe_allow_html=True)

            with c_card3:
                color_unidades = "#16a34a" if tot_registradas >= tot_unidades else "#d97706"
                st.markdown(f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 5px solid {color_unidades}; border-radius: 10px; padding: 14px 18px; min-height: 98px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">Unidades</div>
                    <div style="font-size: 26px; font-weight: 800; color: {color_unidades}; line-height: 1.1;">{tot_registradas} <span style="font-size: 15px; font-weight: 600; color: #94a3b8;">/ {tot_unidades}</span></div>
                </div>
                """, unsafe_allow_html=True)

            with c_card4:
                color_pct = "#16a34a" if progreso >= 1.0 else "#2563eb"
                st.markdown(f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-left: 5px solid {color_pct}; border-radius: 10px; padding: 14px 18px; min-height: 98px; text-align: center; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">% Avance Total</div>
                    <div style="font-size: 26px; font-weight: 800; color: {color_pct}; line-height: 1.1;">{progreso * 100:.1f} %</div>
                </div>
                """, unsafe_allow_html=True)

            st.progress(min(progreso, 1.0))
            st.write("")

            # CONTROL DE RESET DE BUSCADOR
            if "input_scan_val" not in st.session_state:
                st.session_state["input_scan_val"] = ""

            if "mensaje_exito" in st.session_state:
                st.success(st.session_state.pop("mensaje_exito"))

            # ESCÁNER CON CÁMARA
            with st.expander("📷 Escanear con Cámara del Celular / Laptop", expanded=False):
                st.info("💡 Apunta la cámara directamente al código de barras del producto:")
                foto_captura = st.camera_input("Capturar código de barra", key="cam_scanner_input")
                if foto_captura is not None:
                    codigo_leido = decodificar_codigo_imagen(foto_captura)
                    if codigo_leido:
                        st.session_state["input_scan_val"] = codigo_leido
                        st.success(f"🎯 Código de barra detectado: `{codigo_leido}`")
                        st.rerun()
                    else:
                        st.warning("⚠️ No se logró detectar un código de barras claro. Intenta acercar o enfocar mejor.")

            # BUSCADOR ACTIVO
            st.markdown("""
            <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                <span style="font-size: 22px;">⚡</span>
                <span style="font-size: 19px; font-weight: 800; color: #1e293b; letter-spacing: -0.3px;">Pistoleo y Búsqueda Activa</span>
            </div>
            """, unsafe_allow_html=True)

            with st.form("form_escaneo_dinamico", clear_on_submit=False):
                c_in, c_btn = st.columns([4, 1.2])
                with c_in:
                    query = st.text_input(
                        "Pistolea código de barra o escribe nombre/código del producto:",
                        value=st.session_state.get("input_scan_val", ""),
                        placeholder="Ej. 7426500... o CP VERDE o Marcador...",
                        key="input_scan"
                    ).strip().lower()
                with c_btn:
                    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
                    btn_buscar = st.form_submit_button("🔍 Buscar / Pistolear", type="primary", use_container_width=True)

            if query != st.session_state.get("input_scan_val", ""):
                st.session_state["input_scan_val"] = query

            if query:
                query_limpio = query.replace(" ", "").replace("-", "").replace("_", "")
                palabras_busqueda = query.split()

                encontrados = []
                for _, r in df_act.iterrows():
                    if r['_codigo_barra'] and query == str(r['_codigo_barra']).lower():
                        encontrados.append(r)
                        continue

                    valores_fila_texto = " ".join([str(r[c]).lower() for c in columnas_orig if c in r])
                    valores_fila_sin_signos = valores_fila_texto.replace(" ", "").replace("-", "").replace("_", "")

                    if query_limpio and len(query_limpio) >= 3 and query_limpio in valores_fila_sin_signos:
                        encontrados.append(r)
                    elif all(p in valores_fila_texto for p in palabras_busqueda):
                        encontrados.append(r)

                if encontrados:
                    def render_tarjeta_producto(prod, sufijo_key=""):
                        with st.container(border=True):
                            nombre_producto = ""
                            for c in columnas_orig:
                                c_low = c.lower()
                                if any(w in c_low for w in ["desc", "articulo", "detalle", "producto"]):
                                    val = str(prod.get(c, "")).strip()
                                    if val:
                                        nombre_producto = val
                                        break
                            if not nombre_producto:
                                nombre_producto = " | ".join([f"{c}: {prod.get(c, '')}" for c in columnas_orig[:2] if str(prod.get(c, "")).strip()])

                            cod_extra = ""
                            for c in columnas_orig:
                                c_low = c.lower()
                                if ("cod" in c_low or "item" in c_low or "ref" in c_low) and "barra" not in c_low:
                                    val = str(prod.get(c, "")).strip()
                                    if val:
                                        cod_extra = f"<span style='background: #e2e8f0; color: #334155; padding: 2px 8px; border-radius: 4px; font-size: 13px; font-weight: 600; margin-right: 8px;'>CÓD: {val}</span>"
                                        break

                            st.markdown(f"""
                            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 12px 16px; margin-bottom: 14px;">
                                <div style="font-size: 11px; font-weight: 700; color: #64748b; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 4px;">DESCRIPCIÓN DEL ARTÍCULO:</div>
                                <div style="font-size: 20px; font-weight: 800; color: #0f172a; line-height: 1.3;">{cod_extra}{nombre_producto}</div>
                            </div>
                            """, unsafe_allow_html=True)

                            val_precio_compra = "-"
                            val_total = "-"
                            for c in columnas_orig:
                                c_low = c.lower()
                                if ("precio" in c_low or "unit" in c_low or "costo" in c_low) and "total" not in c_low:
                                    if str(prod.get(c, "")).strip():
                                        val_precio_compra = str(prod.get(c, "")).strip()
                                elif "total" in c_low:
                                    if str(prod.get(c, "")).strip():
                                        val_total = str(prod.get(c, "")).strip()

                            faltan_uds = max(0, prod['_cant_total'] - prod['CANT. REGISTRADA'])

                            c_cant, c_reg, c_pcompra, c_ptotal, c_est, c_btns = st.columns([1.0, 1.0, 1.1, 1.1, 1.3, 2.5])
                            
                            with c_cant:
                                st.markdown(
                                    f"<div style='background-color: #f1f3f4; padding: 10px; border-radius: 8px; text-align: center; border-left: 5px solid #1a73e8;'>"
                                    f"<span style='font-size: 11px; color: #5f6368; font-weight: 600;'>CANT. REQUERIDA</span><br>"
                                    f"<strong style='font-size: 28px; color: #1a73e8;'>{prod['_cant_total']}</strong>"
                                    f"</div>",
                                    unsafe_allow_html=True
                                )
                            
                            with c_reg:
                                col_r = "#1e8e3e" if prod['CANT. REGISTRADA'] >= prod['_cant_total'] else ("#d97706" if prod['CANT. REGISTRADA'] > 0 else "#5f6368")
                                st.markdown(
                                    f"<div style='background-color: #f1f3f4; padding: 10px; border-radius: 8px; text-align: center; border-left: 5px solid {col_r};'>"
                                    f"<span style='font-size: 11px; color: #5f6368; font-weight: 600;'>YA REGISTRADAS</span><br>"
                                    f"<strong style='font-size: 28px; color: {col_r};'>{prod['CANT. REGISTRADA']}</strong>"
                                    f"</div>",
                                    unsafe_allow_html=True
                                )

                            with c_pcompra:
                                st.markdown(
                                    f"<div style='background-color: #fef7e0; padding: 10px; border-radius: 8px; text-align: center; border-left: 5px solid #f2994a;'>"
                                    f"<span style='font-size: 11px; color: #5f6368; font-weight: 600;'>PRECIO COMPRA</span><br>"
                                    f"<strong style='font-size: 24px; color: #b05a00;'>{val_precio_compra}</strong>"
                                    f"</div>",
                                    unsafe_allow_html=True
                                )

                            with c_ptotal:
                                st.markdown(
                                    f"<div style='background-color: #fef7e0; padding: 10px; border-radius: 8px; text-align: center; border-left: 5px solid #d97706;'>"
                                    f"<span style='font-size: 11px; color: #5f6368; font-weight: 600;'>TOTAL (L)</span><br>"
                                    f"<strong style='font-size: 24px; color: #92400e;'>{val_total}</strong>"
                                    f"</div>",
                                    unsafe_allow_html=True
                                )

                            with c_est:
                                st.write("")
                                if prod['ESTADO'] == 'Completado':
                                    st.success("✅ COMPLETADO / TACHADO")
                                elif prod['ESTADO'] == 'En Proceso':
                                    st.warning(f"⏳ EN PROCESO ({faltan_uds} rest.)")
                                else:
                                    st.info("⚪ PENDIENTE")

                            with c_btns:
                                st.markdown("<p style='font-weight: 700; margin-bottom: 3px; font-size: 12px; color: #334155;'>⚡ Conteo Rápido / Registro:</p>", unsafe_allow_html=True)
                                
                                desc_corta = nombre_producto[:35]

                                if faltan_uds > 0:
                                    col_input_cant, col_btn_add, col_btn_tach = st.columns([1.1, 1.2, 1.4])
                                    
                                    with col_input_cant:
                                        val_sugerido = faltan_uds if faltan_uds <= 12 else 1
                                        cant_a_sumar = st.number_input(
                                            "Contar",
                                            min_value=1,
                                            max_value=max(1, faltan_uds),
                                            value=val_sugerido,
                                            step=1,
                                            key=f"num_{prod['_id']}_{sufijo_key}",
                                            label_visibility="collapsed"
                                        )
                                    with col_btn_add:
                                        if st.button(f"➕ Sumar {cant_a_sumar}", use_container_width=True, key=f"btn_add_{prod['_id']}_{sufijo_key}"):
                                            actualizar_producto_dinamico(prod['_id'], cant_sumar=int(cant_a_sumar))
                                            if (prod['CANT. REGISTRADA'] + int(cant_a_sumar)) >= prod['_cant_total']:
                                                st.session_state["input_scan_val"] = ""
                                                st.session_state["mensaje_exito"] = f"✅ '{desc_corta}' completado y listo para el siguiente producto."
                                            st.rerun()
                                    with col_btn_tach:
                                        if st.button("✔️ Tachar Todo", type="primary", use_container_width=True, help=f"Registra directamente las {faltan_uds} unidades restantes", key=f"tach_{prod['_id']}_{sufijo_key}"):
                                            actualizar_producto_dinamico(prod['_id'], completar=True)
                                            st.session_state["input_scan_val"] = ""
                                            st.session_state["mensaje_exito"] = f"✅ '{desc_corta}' tachado correctamente ({prod['_cant_total']} unidades)."
                                            st.rerun()
                                else:
                                    st.markdown("<div style='height: 4px;'></div>", unsafe_allow_html=True)
                                    if st.button("↩️ Destachar / Corregir", type="secondary", use_container_width=True, help="Regresa este artículo a 0 unidades registradas y estado Pendiente", key=f"dest_{prod['_id']}_{sufijo_key}"):
                                        actualizar_producto_dinamico(prod['_id'], resetear=True)
                                        st.rerun()

                                if 0 < prod['CANT. REGISTRADA'] < prod['_cant_total']:
                                    if st.button("↩️ Reiniciar a 0 este conteo", type="secondary", use_container_width=True, key=f"dest_parcial_{prod['_id']}_{sufijo_key}"):
                                        actualizar_producto_dinamico(prod['_id'], resetear=True)
                                        st.rerun()

                            st.markdown("<hr style='margin: 10px 0;'>", unsafe_allow_html=True)

                            st.markdown("##### 📑 Datos completos del artículo en la factura:")
                            cols_por_fila = 4
                            grid_cols = st.columns(cols_por_fila)
                            for idx, campo in enumerate(columnas_orig):
                                valor_campo = prod.get(campo, "")
                                c_actual = grid_cols[idx % cols_por_fila]
                                with c_actual:
                                    es_monetario = any(w in campo.lower() for w in ['precio', 'unit', 'costo', 'total', 'isv', 'p. unit'])
                                    color_texto = "#b05a00" if es_monetario else "#202124"
                                    peso_texto = "bold" if es_monetario else "normal"
                                    
                                    st.markdown(
                                        f"<div style='margin-bottom: 8px; background: #fafafa; padding: 6px 10px; border-radius: 6px; border: 1px solid #e0e0e0;'>"
                                        f"<span style='font-size: 11px; color: #70757a; font-weight: 600; text-transform: uppercase;'>{campo}</span><br>"
                                        f"<span style='font-size: 14px; color: {color_texto}; font-weight: {peso_texto};'>{valor_campo if valor_campo != '' else '-'}</span>"
                                        f"</div>",
                                        unsafe_allow_html=True
                                    )

                    if len(encontrados) == 1:
                        st.markdown("### 📌 Producto Seleccionado")
                        render_tarjeta_producto(encontrados[0], sufijo_key="unico")
                    else:
                        st.markdown(f"### 📋 Se encontraron {len(encontrados)} productos coincidentes:")
                        st.caption("Selecciona el producto exacto en la lista desplegable o revisa cada uno abajo con sus botones de registro:")

                        opciones_items = []
                        for idx, p in enumerate(encontrados):
                            desc_candidata = ""
                            for c in columnas_orig:
                                if any(w in c.lower() for w in ["desc", "prod", "articulo", "detalle"]):
                                    desc_candidata = str(p.get(c, ""))
                                    break
                            if not desc_candidata:
                                desc_candidata = " | ".join([f"{c}: {p.get(c, '')}" for c in columnas_orig[:2]])
                            
                            opciones_items.append(f"#{idx+1}: {desc_candidata} (Req: {p['_cant_total']} | Reg: {p['CANT. REGISTRADA']} | {p['ESTADO']})")

                        item_seleccionado_idx = st.selectbox("👇 Elige el producto específico a registrar:", range(len(opciones_items)), format_func=lambda i: opciones_items[i])
                        
                        st.markdown("#### 👉 Producto Seleccionado para Registrar:")
                        render_tarjeta_producto(encontrados[item_seleccionado_idx], sufijo_key="select")

                        with st.expander(f"👁️ Ver todos los {len(encontrados)} resultados coincidentes desplegados hacia abajo", expanded=False):
                            for i_m, prod_multi in enumerate(encontrados):
                                st.markdown(f"**Coincidencia {i_m + 1} de {len(encontrados)}:**")
                                render_tarjeta_producto(prod_multi, sufijo_key=f"list_{i_m}")
                                st.write("")
                else:
                    st.error("❌ No se encontró ningún artículo que coincida con esa búsqueda en esta factura.")

            # TABLA GENERAL
            st.divider()
            st.subheader("📋 Lista Completa con Todas las Columnas del Proveedor")

            def estilo_fila(row):
                if row['ESTADO'] == 'Completado':
                    return ['background-color: #d4edda; color: #155724; text-decoration: line-through;'] * len(row)
                elif row['ESTADO'] == 'En Proceso':
                    return ['background-color: #fff3cd; color: #856404;'] * len(row)
                return [''] * len(row)

            columnas_mostrar = ['ESTADO', 'CANT. REGISTRADA'] + [c for c in columnas_orig if c in df_act.columns]
            df_grid = df_act[columnas_mostrar]

            st.dataframe(df_grid.style.apply(estilo_fila, axis=1), use_container_width=True, height=450)
        else:
            st.info("ℹ️ La factura seleccionada aún no tiene artículos registrados.")
    else:
        st.info("ℹ️ No hay ninguna factura seleccionada. Dirígete a la pestaña **'📥 Cargar / Configurar Nueva Factura'** para subir tu primer PDF.")

# ==============================================================================
# PESTAÑA 2: ÁREA DE CARGA Y CONFIGURACIÓN DE FACTURAS (SEPARADA)
# ==============================================================================
with tab_subida:
    st.subheader("📄 Carga y Configuración de Facturas (PDF)")
    st.caption("Sube el PDF de cualquier proveedor. Esta sección está aislada para no interferir con el trabajo diario de bodega o chequeo.")

    c_nom, c_file = st.columns([1, 2])
    with c_nom:
        nuevo_nom_factura = st.text_input("Nombre o N° de Factura:", placeholder="Ej. Factura_01 o Pacasa_Expo")
    with c_file:
        archivo_pdf = st.file_uploader("Cargar PDF del proveedor:", type=["pdf"], key="nuevo_pdf_tab")

    if archivo_pdf:
        if "df_raw_pdf" not in st.session_state or st.session_state.get("raw_pdf_name") != archivo_pdf.name:
            st.session_state["df_raw_pdf"] = extraer_todas_las_tablas(archivo_pdf)
            st.session_state["raw_pdf_name"] = archivo_pdf.name

        df_raw = st.session_state.get("df_raw_pdf")

        if df_raw is not None and not df_raw.empty:
            st.markdown("#### ⚙️ Configuración de Columnas")
            
            fila_sugerida = auto_detectar_fila_encabezados(df_raw)
            fila_encabezados = st.number_input(
                "Fila donde están los nombres de las columnas (Encabezados reales de productos):",
                min_value=1,
                max_value=len(df_raw),
                value=fila_sugerida,
                step=1,
                help="El sistema autodetectó la fila donde empiezan los títulos reales (ej: CÓDIGO, SKU, DESCRIPCIÓN, CANTIDAD)."
            )
            
            nombres_cols = []
            for idx, col_val in enumerate(df_raw.iloc[fila_encabezados - 1]):
                col_limpia = str(col_val).strip()
                if not col_limpia:
                    col_limpia = f"Columna_{idx+1}"
                if col_limpia in nombres_cols:
                    col_limpia = f"{col_limpia}_{idx+1}"
                nombres_cols.append(col_limpia)

            st.write(f"**Filas leídas en total de todas las páginas del PDF:** `{len(df_raw)}`")
            st.info(f"**Campos detectados ({len(nombres_cols)} columnas):** " + ", ".join(nombres_cols))

            c_op1, c_op2 = st.columns(2)
            with c_op1:
                opciones_barra = ["(Sin Código de Barra)"] + nombres_cols
                idx_def_bar = 0
                for i, c in enumerate(nombres_cols):
                    if any(w in c.lower() for w in ["barra", "bar", "ean", "upc"]):
                        idx_def_bar = i + 1
                        break
                col_barra_elegida = st.selectbox("Columna de Código de Barra (si tiene):", opciones_barra, index=idx_def_bar)
            with c_op2:
                idx_def_cant = next((i for i, c in enumerate(nombres_cols) if "cant" in c.lower() or "qty" in c.lower()), min(1, len(nombres_cols)-1))
                col_cant_elegida = st.selectbox("Columna que contiene la Cantidad:", nombres_cols, index=idx_def_cant)

            df_preview_final = df_raw.iloc[fila_encabezados:].copy()
            df_preview_final.columns = nombres_cols
            st.write("**Vista previa de los primeros productos detectados:**")
            st.dataframe(df_preview_final.head(6), use_container_width=True)

            if st.button("🚀 Guardar en la Nube (Supabase)", type="primary", use_container_width=True):
                if not nuevo_nom_factura.strip():
                    st.error("Asigna un nombre a la factura.")
                else:
                    filas_a_insertar = []
                    encabezados_lower = [str(c).lower().strip() for c in nombres_cols]
                    
                    PALABRAS_DESCARTAR = [
                        "totales consolidados", "total consolidado", "totales", "subtotal", "sub total",
                        "gran total", "suma total", "cotizacion", "cotización", "cotizaciones",
                        "total productos", "total unidades", "piezas", "folios", "folio",
                        "regla de calculo", "regla de cálculo"
                    ]

                    for _, r in df_preview_final.iterrows():
                        datos_dict = r.to_dict()
                        valores_limpios = [str(v).strip() for v in datos_dict.values() if v is not None]
                        if not any(valores_limpios):
                            continue
                        
                        texto_completo_fila = " ".join([str(v).lower() for v in valores_limpios])

                        coincidencias_encabezado = sum(1 for c in nombres_cols if str(r[c]).strip().lower() in encabezados_lower)
                        if coincidencias_encabezado >= 2:
                            continue

                        if any(term in texto_completo_fila for term in PALABRAS_DESCARTAR):
                            continue

                        cant_str = str(datos_dict.get(col_cant_elegida, "1")).strip().replace(",", "")
                        cant_match = re.search(r"[-+]?\d*\.?\d+", cant_str)
                        if not cant_match:
                            continue
                        
                        try:
                            cant_val = int(float(cant_match.group()))
                        except Exception:
                            cant_val = 1
                        
                        if cant_val <= 0:
                            continue

                        cod_barra_val = ""
                        if col_barra_elegida != "(Sin Código de Barra)":
                            cod_barra_val = str(datos_dict.get(col_barra_elegida, "")).strip().replace(" ", "")

                        filas_a_insertar.append({
                            "factura": nuevo_nom_factura.strip(),
                            "datos_json": datos_dict,
                            "codigo_barra": cod_barra_val,
                            "cant_total": cant_val,
                            "cant_registrada": 0,
                            "estado": "Pendiente"
                        })

                    if filas_a_insertar:
                        try:
                            supabase.table("facturas").upsert({
                                "nombre": nuevo_nom_factura.strip(),
                                "columnas_json": nombres_cols,
                                "col_barra": col_barra_elegida,
                                "col_cant": col_cant_elegida
                            }, on_conflict="nombre").execute()

                            supabase.table("productos_dinamicos").delete().eq("factura", nuevo_nom_factura.strip()).execute()

                            batch_size = 100
                            for i in range(0, len(filas_a_insertar), batch_size):
                                lote = filas_a_insertar[i:i + batch_size]
                                supabase.table("productos_dinamicos").insert(lote).execute()

                            st.success(f"¡Factura '{nuevo_nom_factura}' guardada limpiamente con {len(filas_a_insertar)} productos!")
                            st.session_state.pop("df_raw_pdf", None)
                            st.session_state.pop("raw_pdf_name", None)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar en Supabase: {e}")
