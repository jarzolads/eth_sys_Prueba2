import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
from PIL import Image

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILO
# ==========================================
st.set_page_config(page_title="IALabs - BioSTEAM Interface", layout="wide")

st.markdown("""
    <style>
    .main { background-color: #f5f7f9; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_input):
    # Limpiar flujos previos para evitar errores de ID duplicado
    bst.main_flowsheet.clear() 
    
    # Termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Corrientes
    mosto = bst.Stream("1-MOSTO", Water=flow_water, Ethanol=flow_ethanol, 
                       units="kg/hr", T=temp_input + 273.15)
    vinazas_retorno = bst.Stream("Vinazas-Retorno", Water=200, T=95+273.15)

    # Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                        outs=("3-Mosto-Pre", "Drenaje"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="Mezcla", T=92+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="Mezcla-Bif", P=101325)
    
    # El Flash puede dar error si intentas acceder a .duty directamente
    V1 = bst.Flash("V1", ins=V100-0, outs=("Vapor-caliente", "Vinazas"), P=101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="Producto-Final", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Sistema
    sys = bst.System("etanol_sys", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    return sys, W310.outs[0]

def generar_tablas(sistema):
    # Tabla de Materia
    datos_mat = []
    for s in sistema.streams:
        if s.F_mass > 0:
            datos_mat.append({
                "ID Corriente": s.ID,
                "Temp (°C)": round(s.T - 273.15, 2),
                "Flujo (kg/h)": round(s.F_mass, 2),
                "% Etanol": f"{(s.imass['Ethanol']/s.F_mass)*100:.1f}%" if s.F_mass > 0 else "0%"
            })
    df_mat = pd.DataFrame(datos_mat)

    # Tabla de Energía
    datos_en = []
    for u in sistema.units:
        calor_kw = 0.0
        if isinstance(u, bst.HXprocess):
            calor_kw = (u.outs[0].H - u.ins[0].H) / 3600
        elif hasattr(u, 'heat_utilities') and u.heat_utilities:
            calor_kw = sum([hu.duty for hu in u.heat_utilities]) / 3600
            
        if abs(calor_kw) > 0.001:
            datos_en.append({"Equipo": u.ID, "Carga Térmica (kW)": round(calor_kw, 2)})
    
    return df_mat, pd.DataFrame(datos_en)

# ==========================================
# 3. INTERFAZ DE USUARIO (UI)
# ==========================================
st.sidebar.image("https://biosteam.readthedocs.io/en/latest/_static/biosteam_logo.png", width=200)
st.sidebar.title("Configuración IALabs")

with st.sidebar:
    st.subheader("Variables de Proceso")
    f_w = st.slider("Agua en alimentación (kg/h)", 500, 2000, 900)
    f_e = st.slider("Etanol en alimentación (kg/h)", 10, 500, 100)
    t_in = st.number_input("Temperatura Entrada (°C)", 10, 50, 25)
    
    st.divider()
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    ask_ai = st.checkbox("Habilitar Tutor IA")

# --- Ejecución ---
if st.sidebar.button("🚀 Ejecutar Simulación", use_container_width=True):
    with st.spinner("Calculando balances..."):
        sistema, producto = run_simulation(f_w, f_e, t_in)
        df_m, df_e = generar_tablas(sistema)
        
        # KPIs Principales
        col1, col2, col3 = st.columns(3)
        pureza = (producto.imass['Ethanol'] / producto.F_mass) * 100
        col1.metric("Pureza Etanol", f"{pureza:.2f} %", delta=f"{pureza-10:.1f}% vs Mosto")
        col2.metric("Producción", f"{producto.F_mass:.1f} kg/h")
        col3.metric("Temp. Salida", f"{producto.T-273.15:.1f} °C")

        # Visualización de Tablas
        tab1, tab2, tab3 = st.tabs(["📊 Balance de Materia", "⚡ Energía", "📐 Diagrama PFD"])
        
        with tab1:
            st.dataframe(df_m, use_container_width=True)
        
        with tab2:
            st.table(df_e)
            
        with tab3:
            try:
                # Generar diagrama y mostrarlo
                sistema.diagram(file="pfd", format="png", display=False)
                st.image("pfd.png", caption="Diagrama de Flujo del Proceso (PFD)")
            except:
                st.error("Instala 'graphviz' en el sistema para ver el diagrama.")

        # Integración con Gemini
        if ask_ai and api_key:
            st.divider()
            st.subheader("🤖 Análisis del Tutor IA")
            try:
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel('gemini-2.5-pro')
                contexto = f"Simulación de purificación: Entra {f_e}kg/h etanol y {f_w}kg/h agua. Sale con {pureza:.2f}% pureza. Datos: {df_m.to_string()}"
                response = model.generate_content(f"Como ingeniero químico, analiza estos resultados brevemente: {contexto}")
                st.info(response.text)
            except Exception as e:
                st.warning(f"No se pudo conectar con Gemini: {e}")

else:
    st.info("Ajusta los parámetros en la barra lateral y presiona 'Ejecutar Simulación' para comenzar.")
