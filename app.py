import json
import time
from datetime import datetime

import pandas as pd
import plotly.express as px
import streamlit as st
import serial
from serial.tools import list_ports
from streamlit_autorefresh import st_autorefresh

# ---------------------------------------------------------------------
# AURAmbient | Monitor multiparámetro de calidad del agua
# Protocolo serial esperado:
#   JSON: {"ph":7.18,"std":305,"turbidez":2.1,"temperatura":24.3}
#   CSV:  7.18,305,2.1,24.3
# Orden CSV: pH, STD (ppm), turbidez (NTU), temperatura (°C)
# ---------------------------------------------------------------------

st.set_page_config(
    page_title="AURAmbient | Monitor de agua",
    page_icon="💧",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Colores inspirados en el PDF de referencia
BG = "#061C2B"
PANEL = "#0B2B40"
PANEL_2 = "#0E354C"
CYAN = "#20D9F2"
BLUE = "#168BCE"
GREEN = "#31E58D"
TEXT = "#EAF6FF"
MUTED = "#9AB8CA"
RED = "#FF657A"
YELLOW = "#FFC857"

st.markdown(
    f"""
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
      html, body, [class*="css"] {{ font-family: Inter, sans-serif; }}
      .stApp {{ background: radial-gradient(circle at 80% 0%, #0b3048 0%, {BG} 48%); color:{TEXT}; }}
      [data-testid="stSidebar"] {{ background:#082438; border-right:1px solid #17445B; }}
      [data-testid="stSidebar"] * {{ color:{TEXT}; }}
      h1,h2,h3,h4,p,label {{ color:{TEXT}; }}
      .aur-top {{ display:flex; align-items:center; gap:14px; padding:4px 0 18px 0; border-bottom:1px solid #1B4A61; margin-bottom:22px; }}
      .aur-logo {{ font-size:25px; font-weight:800; letter-spacing:-1px; }}
      .aur-logo span {{ color:{GREEN}; }}
      .aur-sub {{ color:{MUTED}; font-size:12px; border-left:1px solid #286078; padding-left:14px; }}
      .eyebrow {{ color:{CYAN}; font-size:11px; font-weight:700; letter-spacing:2px; text-transform:uppercase; }}
      .main-title {{ font-size:30px; font-weight:800; margin:2px 0 0 0; }}
      .subtitle {{ color:{MUTED}; font-size:13px; margin-top:3px; }}
      .metric-card {{ background:linear-gradient(145deg,{PANEL_2},{PANEL}); border:1px solid #17617A; border-radius:15px; padding:17px 18px 15px 18px; min-height:135px; box-shadow:0 8px 25px #00101c55; }}
      .metric-head {{ color:#B6D2E2; font-size:13px; font-weight:600; }}
      .metric-value {{ color:{TEXT}; font-size:31px; font-weight:800; line-height:1.3; margin-top:8px; }}
      .metric-unit {{ font-size:13px; color:{MUTED}; font-weight:500; }}
      .metric-status {{ font-size:11px; margin-top:8px; font-weight:600; }}
      .dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:6px; }}
      .panel {{ background:linear-gradient(145deg,#0D3046,#092538); border:1px solid #174D65; border-radius:16px; padding:17px 18px; }}
      .panel-title {{ font-size:15px; font-weight:700; margin-bottom:10px; }}
      .status-big {{ font-size:19px; font-weight:800; color:{GREEN}; }}
      .small-muted {{ color:{MUTED}; font-size:12px; }}
      .pill {{ border:1px solid #23617A; background:#0B344B; border-radius:20px; padding:5px 10px; color:#BCEBFA; font-size:11px; }}
      div[data-testid="stMetric"] {{ background:transparent; }}
      .stButton>button {{ background:linear-gradient(90deg,#087EAC,#12BBD3); color:white; border:0; border-radius:9px; font-weight:700; }}
      .stButton>button:hover {{ border:1px solid {CYAN}; color:white; }}
      div[data-baseweb="select"]>div, div[data-baseweb="input"]>div {{ background:#0A2B40; border-color:#20536A; }}
      .stTabs [data-baseweb="tab-list"] {{ gap:8px; }}
      .stTabs [data-baseweb="tab"] {{ background:#0A2B40; border-radius:8px 8px 0 0; color:{MUTED}; }}
      hr {{ border-color:#1A4B62; }}
      footer {{ visibility:hidden; }}
    </style>
    """,
    unsafe_allow_html=True,
)

DEFAULT_LIMITS = {
    "pH": (6.5, 8.5),
    "STD": (0, 1000),
    "Turbidez": (0, 5),
    "Temperatura": (5, 35),
}
SENSOR_META = {
    "pH": ("pH", "", "💧"),
    "STD": ("Sólidos totales disueltos", "ppm", "◉"),
    "Turbidez": ("Turbidez", "NTU", "◌"),
    "Temperatura": ("Temperatura", "°C", "🌡️"),
}
KEYS = ["ph", "std", "turbidez", "temperatura"]

if "history" not in st.session_state:
    st.session_state.history = []
if "serial_conn" not in st.session_state:
    st.session_state.serial_conn = None
if "serial_port" not in st.session_state:
    st.session_state.serial_port = None
if "last_error" not in st.session_state:
    st.session_state.last_error = ""
if "latest" not in st.session_state:
    st.session_state.latest = None
if "limits" not in st.session_state:
    st.session_state.limits = DEFAULT_LIMITS.copy()
if "calibration_log" not in st.session_state:
    st.session_state.calibration_log = []

def close_serial():
    conn = st.session_state.get("serial_conn")
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    st.session_state.serial_conn = None
    st.session_state.serial_port = None

def normalize_reading(obj):
    """Convierte una trama JSON o CSV a valores numéricos."""
    if isinstance(obj, dict):
        aliases = {
            "ph": ["ph", "pH"],
            "std": ["std", "tds", "STD", "TDS"],
            "turbidez": ["turbidez", "turbidity", "ntu"],
            "temperatura": ["temperatura", "temperature", "temp"],
        }
        result = {}
        for target, names in aliases.items():
            for name in names:
                if name in obj:
                    result[target] = float(obj[name])
                    break
            else:
                raise ValueError(f"Falta el campo '{target}' en la trama.")
    else:
        parts = [x.strip() for x in str(obj).replace(";", ",").split(",")]
        if len(parts) != 4:
            raise ValueError("La trama CSV debe tener 4 valores: pH,STD,turbidez,temperatura.")
        result = dict(zip(KEYS, map(float, parts)))
    if not all(pd.notna(v) for v in result.values()):
        raise ValueError("La trama contiene valores no numéricos.")
    return result

def parse_line(line):
    line = line.strip()
    if not line:
        return None
    if line.startswith("{"):
        return normalize_reading(json.loads(line))
    return normalize_reading(line)

def add_reading(values, source):
    now = datetime.now()
    row = {
        "fecha": now,
        "hora": now.strftime("%H:%M:%S"),
        "origen": source,
        **values,
    }
    st.session_state.latest = row
    hist = st.session_state.history
    hist.append(row)
    # Mantener memoria acotada en sesiones largas
    st.session_state.history = hist[-3000:]

def read_serial_buffer():
    conn = st.session_state.serial_conn
    if conn is None or not conn.is_open:
        return
    # Leer todas las líneas disponibles sin bloquear la interfaz
    for _ in range(30):
        try:
            if conn.in_waiting <= 0:
                break
            raw = conn.readline().decode("utf-8", errors="ignore").strip()
            if raw:
                values = parse_line(raw)
                if values:
                    add_reading(values, "Arduino")
                    st.session_state.last_error = ""
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            st.session_state.last_error = f"Trama ignorada: {exc}"
        except serial.SerialException as exc:
            st.session_state.last_error = f"Error serial: {exc}"
            close_serial()
            break

def within_range(name, value):
    low, high = st.session_state.limits[name]
    return low <= value <= high

def status_for(name, value):
    if within_range(name, value):
        return "Dentro del rango", GREEN
    return "Fuera del rango", RED

# Sidebar: conexión y navegación
with st.sidebar:
    st.markdown(
        f'<div style="padding:8px 0 22px"><div class="aur-logo">AURA<span>mbient</span></div>'
        f'<div class="small-muted">Sistema multiparámetro de agua</div></div>',
        unsafe_allow_html=True,
    )
    page = st.radio(
        "NAVEGACIÓN",
        ["Inicio", "Tendencias", "Calibración", "Alarmas", "Ajustes"],
        label_visibility="visible",
    )
    st.divider()
    st.markdown("**CONEXIÓN ARDUINO**")
    ports = [p.device for p in list_ports.comports()]
    port_options = ports if ports else ["Sin puertos detectados"]
    selected_port = st.selectbox("Puerto serial", port_options, disabled=not ports)
    baud = st.selectbox("Velocidad (baud)", [9600, 19200, 38400, 57600, 115200], index=0)
    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("Conectar", use_container_width=True, disabled=not ports):
            try:
                close_serial()
                st.session_state.serial_conn = serial.Serial(
                    selected_port, baudrate=baud, timeout=0.05
                )
                st.session_state.serial_port = selected_port
                st.session_state.last_error = ""
                time.sleep(1.5)  # Arduino puede reiniciarse al abrir el puerto
                st.rerun()
            except Exception as exc:
                st.session_state.last_error = f"No se pudo conectar: {exc}"
    with col_b:
        if st.button("Desconectar", use_container_width=True):
            close_serial()
            st.rerun()

    connected = (
        st.session_state.serial_conn is not None
        and getattr(st.session_state.serial_conn, "is_open", False)
    )
    if connected:
        st.success(f"En línea · {st.session_state.serial_port}")
    else:
        st.warning("Arduino desconectado")
    demo = st.toggle("Modo demostración", value=not connected, disabled=connected)
    if st.session_state.last_error:
        st.error(st.session_state.last_error)
    st.divider()
    st.caption("AURAmbient · Interfaz conceptual")

# Actualización periódica para lectura serial
if connected:
    st_autorefresh(interval=1000, key="serial_refresh")
    read_serial_buffer()
elif demo:
    st_autorefresh(interval=2000, key="demo_refresh")
    if not st.session_state.history or st.session_state.history[-1]["origen"] != "Demo" or (
        datetime.now() - st.session_state.history[-1]["fecha"]
    ).total_seconds() >= 1.8:
        import random
        previous = st.session_state.latest or {
            "ph": 7.18, "std": 305.0, "turbidez": 2.1, "temperatura": 24.3
        }
        sample = {
            "ph": round(max(0, min(14, previous["ph"] + random.uniform(-0.04, 0.04))), 2),
            "std": round(max(0, previous["std"] + random.uniform(-3, 3)), 1),
            "turbidez": round(max(0, previous["turbidez"] + random.uniform(-0.08, 0.08)), 2),
            "temperatura": round(previous["temperatura"] + random.uniform(-0.12, 0.12), 2),
        }
        add_reading(sample, "Demo")

# Encabezado
st.markdown(
    f"""
    <div class="aur-top">
      <div class="aur-logo">AURA<span>mbient</span></div>
      <div class="aur-sub">SISTEMA MULTIPARÁMETRO DE CALIDAD DEL AGUA</div>
      <div style="margin-left:auto" class="pill">● {"EN LÍNEA" if connected else ("DEMO" if demo else "SIN DATOS")}</div>
    </div>
    """,
    unsafe_allow_html=True,
)

if st.session_state.latest is None:
    st.info("Conecta el Arduino desde el menú lateral o activa el modo demostración para visualizar la interfaz.")
    st.stop()

latest = st.session_state.latest
history_df = pd.DataFrame(st.session_state.history)
history_df = history_df.sort_values("fecha")

def metric_card(name, value, unit, icon):
    state, color = status_for(name, value)
    return f"""
    <div class="metric-card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div class="metric-head">{icon} &nbsp; {name}</div>
        <span style="color:{color};font-size:18px">●</span>
      </div>
      <div class="metric-value">{value:g} <span class="metric-unit">{unit}</span></div>
      <div class="metric-status" style="color:{color}">● &nbsp;{state}</div>
    </div>
    """

def plot_parameter(data, key, label, unit):
    fig = px.line(data, x="fecha", y=key, markers=True)
    fig.update_traces(line=dict(color=CYAN, width=3), marker=dict(size=5, color="#EAFBFF"))
    fig.update_layout(
        height=330, margin=dict(l=8, r=8, t=15, b=8),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#B7D1E0", family="Inter"),
        xaxis=dict(title="", showgrid=False, zeroline=False),
        yaxis=dict(title=unit, gridcolor="#1B4358", zeroline=False),
        hovermode="x unified",
    )
    return fig

# Contenido de páginas
if page == "Inicio":
    st.markdown('<div class="eyebrow">MONITOREO EN TIEMPO REAL</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-title">Calidad del agua</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Visualización simultánea de parámetros y estado de los sensores.</div>', unsafe_allow_html=True)
    st.write("")
    cols = st.columns(4, gap="medium")
    for col, name, key in zip(cols, ["pH", "STD", "Turbidez", "Temperatura"], KEYS):
        title, unit, icon = SENSOR_META[name]
        with col:
            st.markdown(metric_card(name, latest[key], unit, icon), unsafe_allow_html=True)

    st.write("")
    left, right = st.columns([2.1, 1], gap="large")
    with left:
        st.markdown('<div class="panel"><div class="panel-title">◈ &nbsp; Tendencia reciente</div>', unsafe_allow_html=True)
        parameter = st.selectbox("Parámetro", ["pH", "STD", "Turbidez", "Temperatura"], label_visibility="collapsed")
        key = dict(zip(["pH", "STD", "Turbidez", "Temperatura"], KEYS))[parameter]
        unit = SENSOR_META[parameter][1]
        st.plotly_chart(plot_parameter(history_df.tail(100), key, parameter, unit), use_container_width=True)
        st.markdown("</div>", unsafe_allow_html=True)
    with right:
        out_of_range = [
            n for n, k in zip(["pH", "STD", "Turbidez", "Temperatura"], KEYS)
            if not within_range(n, latest[k])
        ]
        if out_of_range:
            st.markdown(
                '<div class="panel"><div class="panel-title">◈ &nbsp; Estado general</div>'
                '<div class="status-big" style="color:#FF657A">Revisar parámetros</div>'
                f'<p class="small-muted">Fuera del rango configurado: {", ".join(out_of_range)}.</p></div>',
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                '<div class="panel"><div class="panel-title">◈ &nbsp; Estado general</div>'
                '<div class="status-big">✓ Parámetros en rango</div>'
                '<p class="small-muted">Las lecturas están dentro de los límites definidos en Ajustes.</p></div>',
                unsafe_allow_html=True,
            )
        st.write("")
        st.markdown(
            f'<div class="panel"><div class="panel-title">♧ &nbsp; Alertas</div>'
            f'<div class="status-big" style="font-size:16px;color:{RED if out_of_range else GREEN}">'
            f'{"⚠ " + str(len(out_of_range)) + " alerta(s)" if out_of_range else "✓ Sin alertas activas"}</div>'
            f'<p class="small-muted">Última lectura: {latest["fecha"].strftime("%d/%m/%Y %H:%M:%S")}</p>'
            f'<p class="small-muted">Origen: {latest["origen"]}</p></div>',
            unsafe_allow_html=True,
        )

elif page == "Tendencias":
    st.markdown('<div class="eyebrow">HISTORIAL DE MEDICIONES</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-title">Tendencias</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Evolución temporal de los parámetros registrados en esta sesión.</div>', unsafe_allow_html=True)
    options = ["pH", "STD", "Turbidez", "Temperatura"]
    selected = st.selectbox("Selecciona el parámetro", options)
    key = dict(zip(options, KEYS))[selected]
    unit = SENSOR_META[selected][1]
    st.markdown('<div class="panel">', unsafe_allow_html=True)
    st.plotly_chart(plot_parameter(history_df, key, selected, unit), use_container_width=True)
    c1, c2, c3 = st.columns(3)
    c1.metric("Mínimo", f'{history_df[key].min():.2f} {unit}')
    c2.metric("Promedio", f'{history_df[key].mean():.2f} {unit}')
    c3.metric("Máximo", f'{history_df[key].max():.2f} {unit}')
    st.markdown("</div>", unsafe_allow_html=True)
    st.write("")
    st.markdown("**Registro de lecturas**")
    st.dataframe(history_df[["fecha", "ph", "std", "turbidez", "temperatura", "origen"]].tail(100), use_container_width=True, hide_index=True)
    csv = history_df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("Descargar historial CSV", csv, "aurambient_historial.csv", "text/csv")

elif page == "Calibración":
    st.markdown('<div class="eyebrow">MANTENIMIENTO DE SENSORES</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-title">Calibración</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Registro de puntos de referencia y observaciones de calibración.</div>', unsafe_allow_html=True)
    st.warning("Este apartado documenta la calibración; no envía comandos de calibración al Arduino. Sigue el procedimiento del fabricante de cada sensor.")
    with st.form("calibration_form"):
        sensor = st.selectbox("Sensor", ["pH", "STD", "Turbidez", "Temperatura"])
        reference = st.number_input("Valor de referencia", value=7.00, format="%.3f")
        measured = st.number_input("Valor indicado por el sistema", value=float(latest[dict(zip(["pH","STD","Turbidez","Temperatura"], KEYS))[sensor]]), format="%.3f")
        notes = st.text_input("Solución patrón / observaciones")
        submitted = st.form_submit_button("Guardar registro")
        if submitted:
            st.session_state.calibration_log.append({
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "sensor": sensor, "referencia": reference,
                "lectura": measured, "diferencia": measured-reference,
                "observaciones": notes,
            })
            st.success("Registro de calibración guardado en esta sesión.")
    st.markdown("**Historial de calibración**")
    if st.session_state.calibration_log:
        st.dataframe(pd.DataFrame(st.session_state.calibration_log), use_container_width=True, hide_index=True)
    else:
        st.caption("Aún no hay registros.")

elif page == "Alarmas":
    st.markdown('<div class="eyebrow">SUPERVISIÓN</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-title">Alarmas</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Notificaciones según los límites configurados.</div>', unsafe_allow_html=True)
    alarms = []
    for name, key in zip(["pH", "STD", "Turbidez", "Temperatura"], KEYS):
        low, high = st.session_state.limits[name]
        value = latest[key]
        if value < low or value > high:
            alarms.append((name, value, low, high))
    if alarms:
        for name, value, low, high in alarms:
            st.error(f"{name}: lectura {value:g}. Límite configurado: {low:g} – {high:g}.")
    else:
        st.success("Sin alertas activas. Todas las lecturas están dentro de los límites configurados.")
    st.caption("Las alarmas son indicativas y dependen de los límites que configures.")

elif page == "Ajustes":
    st.markdown('<div class="eyebrow">CONFIGURACIÓN DEL SISTEMA</div>', unsafe_allow_html=True)
    st.markdown('<div class="main-title">Ajustes</div>', unsafe_allow_html=True)
    st.markdown('<div class="subtitle">Personaliza los límites de referencia para las alertas.</div>', unsafe_allow_html=True)
    with st.form("limits_form"):
        new_limits = {}
        for name in ["pH", "STD", "Turbidez", "Temperatura"]:
            current_low, current_high = st.session_state.limits[name]
            st.markdown(f"**{name}**")
            c1, c2 = st.columns(2)
            with c1:
                low = st.number_input(f"Mínimo · {name}", value=float(current_low), key=f"low_{name}")
            with c2:
                high = st.number_input(f"Máximo · {name}", value=float(current_high), key=f"high_{name}")
            new_limits[name] = (low, high)
        save = st.form_submit_button("Guardar límites")
        if save:
            invalid = [n for n, (lo, hi) in new_limits.items() if lo >= hi]
            if invalid:
                st.error("El mínimo debe ser menor que el máximo: " + ", ".join(invalid))
            else:
                st.session_state.limits = new_limits
                st.success("Límites actualizados.")
    st.divider()
    st.markdown("**Información de conexión**")
    st.write(f"Estado: {'Conectado' if connected else 'Desconectado'}")
    st.write(f"Puerto: {st.session_state.serial_port or '—'}")
    st.write(f"Baud rate: {baud}")

st.divider()
st.markdown(
    '<div class="small-muted">AURAmbient · Sistema de monitoreo multiparámetro · '
    'Las lecturas y alertas no certifican la calidad ni la potabilidad del agua.</div>',
    unsafe_allow_html=True,
)
