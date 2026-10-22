import streamlit as st
import pandas as pd
import pdfplumber
import json
import re
from supabase import create_client, Client

st.set_page_config(page_title="Control Universal de Facturas", page_icon="📦", layout="wide")

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

def actualizar_producto_dinamico(prod_id, cant_sumar=None, completar=False):
    try:
        res = supabase.table("productos_dinamicos").select("cant_total, cant_registrada").eq("id", prod_id).execute()
        if res.data:
            row = res.data[0]
            cant_total = row["cant_total"]
            cant_reg = row["cant_registrada"]
            nueva_cant = cant_total if completar else cant_reg + (cant_sumar or 1)
            nuevo_estado = "Completado" if nueva_cant >= cant_total else ("En Proceso" if nueva_cant > 0 else "Pendiente")
            
            supabase.table("productos_dinamicos").update({
                "cant_registrada": nueva_cant,
                "estado": nuevo_estado
            }).eq("id", prod_id).execute()
    except Exception as e:
        st.error(f"Error al actualizar: {e}")

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

st.title("📦 Sistema Universal de Facturas y Pistoleo")

with st.sidebar:
    st.header("📂 Facturas Disponibles")
    facturas_lista = obtener_facturas()
    factura_activa = st.selectbox("Seleccionar Factura a trabajar:", facturas_lista if facturas_lista else ["Sin facturas"])
    if st.button("🔄 Refrescar datos"):
        st.rerun()
    st.divider()

# SECCIÓN DE SUBIDA DE FACTURAS
with st.expander("➕ Subir Factura PDF (Cualquier Proveedor)", expanded=(factura_activa == "Sin facturas")):
    c_nom, c_file = st.columns([1, 2])
    with c_nom:
        nuevo_nom_factura = st.text_input("Nombre o N° de Factura:", placeholder="Ej. Factura_01")
    with c_file:
        archivo_pdf = st.file_uploader("Cargar PDF del proveedor:", type=["pdf"], key="nuevo_pdf")

    if archivo_pdf:
        if "df_raw_pdf" not in st.session_state or st.session_state.get("raw_pdf_name") != archivo_pdf.name:
            st.session_state["df_raw_pdf"] = extraer_todas_las_tablas(archivo_pdf)
            st.session_state["raw_pdf_name"] = archivo_pdf.name

        df_raw = st.session_state.get("df_raw_pdf")

        if df_raw is not None and not df_raw.empty:
            st.subheader("Configuración de columnas de esta factura")
            fila_encabezados = st.number_input("Fila donde están los nombres de las columnas (Encabezados):", min_value=1, max_value=len(df_raw), value=1, step=1)
            
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
            st.write("**Vista previa de los primeros productos:**")
            st.dataframe(df_preview_final.head(6), use_container_width=True)

            if st.button("🚀 Guardar en la Nube (Supabase)", type="primary"):
                if not nuevo_nom_factura.strip():
                    st.error("Asigna un nombre a la factura.")
                else:
                    filas_a_insertar = []
                    encabezados_lower = [str(c).lower() for c in nombres_cols]
                    
                    # Palabras que identifican filas de totales a excluir
                    PALABRAS_TOTALES = ["totales consolidados", "total consolidado", "totales", "subtotal", "sub total", "gran total", "suma total"]

                    for _, r in df_preview_final.iterrows():
                        datos_dict = r.to_dict()
                        valores_limpios = [str(v).strip() for v in datos_dict.values() if v is not None]
                        if not any(valores_limpios):
                            continue
                        
                        # 1. Descartar si es repetición de encabezado entre páginas
                        if any(str(r[c]).strip().lower() in encabezados_lower for c in nombres_cols[:2]):
                            continue

                        # 2. Descartar si es una fila de totales / consolidado
                        texto_completo_fila = " ".join([str(v).lower() for v in valores_limpios])
                        if any(term in texto_completo_fila for term in PALABRAS_TOTALES):
                            continue

                        # 3. Validar que tenga código de barra o al menos un código/descripción real
                        cod_barra_val = ""
                        if col_barra_elegida != "(Sin Código de Barra)":
                            cod_barra_val = str(datos_dict.get(col_barra_elegida, "")).strip().replace(" ", "")

                        cant_str = str(datos_dict.get(col_cant_elegida, "1")).strip().replace(",", "")
                        try:
                            cant_match = re.search(r"[-+]?\d*\.?\d+", cant_str)
                            cant_val = int(float(cant_match.group())) if cant_match else 1
                        except Exception:
                            cant_val = 1
                        
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

                            st.success(f"¡Factura '{nuevo_nom_factura}' guardada sin filas de totales con {len(filas_a_insertar)} productos reales!")
                            st.session_state.pop("df_raw_pdf", None)
                            st.session_state.pop("raw_pdf_name", None)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Error al guardar en Supabase: {e}")

# --- ÁREA OPERATIVA DE PISTOLEO Y BÚSQUEDA ---
if factura_activa and factura_activa != "Sin facturas":
    columnas_orig, col_barra_cfg, col_cant_cfg = obtener_info_factura(factura_activa)
    df_act = cargar_productos_factura(factura_activa)

    if not df_act.empty:
        tot_unidades = int(df_act['_cant_total'].sum())
        tot_registradas = int(df_act['CANT. REGISTRADA'].sum())
        tot_items = len(df_act)
        items_listos = len(df_act[df_act['ESTADO'] == 'Completado'])
        progreso = (tot_registradas / tot_unidades) if tot_unidades > 0 else 0.0

        c_m1, c_m2, c_m3, c_m4 = st.columns(4)
        c_m1.metric("Factura Activa", factura_activa)
        c_m2.metric("Ítems Listos", f"{items_listos} / {tot_items}")
        c_m3.metric("Unidades Registradas", f"{tot_registradas} / {tot_unidades}")
        c_m4.metric("% Avance Total", f"{progreso * 100:.1f} %")

        st.progress(min(progreso, 1.0))
        st.divider()

        st.subheader("🔍 Cuadro de Búsqueda y Pistoleo")
        with st.form("form_escaneo_dinamico", clear_on_submit=False):
            c_in, c_btn = st.columns([4, 1])
            with c_in:
                query = st.text_input("Pistolea código de barra o escribe código/descripción (ej: marcador tablero o lapiz infinito):", key="input_scan").strip().lower()
            with c_btn:
                btn_buscar = st.form_submit_button("Buscar / Escanear", use_container_width=True)

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

                        c_cant, c_reg, c_pcompra, c_ptotal, c_est, c_btns = st.columns([1.1, 1.1, 1.2, 1.2, 1.4, 1.8])
                        
                        with c_cant:
                            st.markdown(
                                f"<div style='background-color: #f1f3f4; padding: 10px; border-radius: 8px; text-align: center; border-left: 5px solid #1a73e8;'>"
                                f"<span style='font-size: 11px; color: #5f6368; font-weight: 600;'>CANT. REQUERIDA</span><br>"
                                f"<strong style='font-size: 28px; color: #1a73e8;'>{prod['_cant_total']}</strong>"
                                f"</div>",
                                unsafe_allow_html=True
                            )
                        
                        with c_reg:
                            col_r = "#1e8e3e" if prod['CANT. REGISTRADA'] >= prod['_cant_total'] else "#d93025"
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
                                faltan = prod['_cant_total'] - prod['CANT. REGISTRADA']
                                st.warning(f"⏳ EN PROCESO ({faltan} rest.)")
                            else:
                                st.info("⚪ PENDIENTE")

                        with c_btns:
                            st.markdown("<p style='font-weight: 600; margin-bottom: 3px; font-size: 13px;'>Acciones Rápidas:</p>", unsafe_allow_html=True)
                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button("➕ Sumar 1", use_container_width=True, key=f"sum_{prod['_id']}_{sufijo_key}"):
                                    actualizar_producto_dinamico(prod['_id'], cant_sumar=1)
                                    st.rerun()
                            with b2:
                                if st.button("✔️ Tachar / Todo", type="primary", use_container_width=True, key=f"tach_{prod['_id']}_{sufijo_key}"):
                                    actualizar_producto_dinamico(prod['_id'], completar=True)
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

        # TABLA COMPLETA
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
