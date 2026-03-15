import streamlit as st
import biosteam as bst
import thermosteam as tmo
import pandas as pd
import google.generativeai as genai
import os

# ==========================================
# 1. CONFIGURACIÓN DE LA PÁGINA Y ESTILO
# ==========================================
st.set_page_config(page_title="IALabs - BioSTEAM Interface", layout="wide")

# Estilo personalizado para una interfaz más "limpia"
st.markdown("""
    <style>
    .main { background-color: #f8f9fa; }
    .stMetric { background-color: #ffffff; padding: 15px; border-radius: 10px; border: 1px solid #e0e0e0; }
    </style>
    """, unsafe_allow_html=True)

# ==========================================
# 2. LÓGICA DE SIMULACIÓN (ENCAPSULADA)
# ==========================================
def run_simulation(flow_water, flow_ethanol, temp_input):
    # CRÍTICO: Limpiar el flowsheet para evitar errores de ID duplicado en cada rerun
    bst.main_flowsheet.clear() 
    
    # Definición de componentes y termodinámica
    chemicals = tmo.Chemicals(["Water", "Ethanol"])
    bst.settings.set_thermo(chemicals)

    # Definición de Corrientes
    mosto = bst.Stream("MOSTO_ALIM", Water=flow_water, Ethanol=flow_ethanol, 
                       units="kg/hr", T=temp_input + 273.15)
    
    # Corriente de reciclo inicializada
    vinazas_retorno = bst.Stream("VINAZAS_RET", Water=200, T=95+273.15)

    # Configuración de Equipos
    P100 = bst.Pump("P100", ins=mosto, P=4*101325)
    
    W210 = bst.HXprocess("W210", ins=(P100-0, vinazas_retorno), 
                        outs=("MOST_PRE", "DRENAJE"), phase0="l", phase1="l")
    W210.outs[0].T = 85 + 273.15
    
    W220 = bst.HXutility("W220", ins=W210-0, outs="MEZCLA_CAL", T=92+273.15)
    V100 = bst.IsenthalpicValve("V100", ins=W220-0, outs="MEZCLA_BIF", P=101325)
    
    # Separador Flash
    V1 = bst.Flash("V1", ins=V100-0, outs=("VAPOR_CAL", "VINAZAS_FONDO"), P=101325, Q=0)
    
    W310 = bst.HXutility("W310", ins=V1-0, outs="PRODUCTO_FINAL", T=25+273.15)
    P200 = bst.Pump("P200", ins=V1-1, outs=vinazas_retorno, P=3*101325)

    # Crear y Simular Sistema
    sys = bst.System("etanol_sys", path=(P100, W210, W220, V100, V1, W310, P200))
    sys.simulate()
    
    return sys, W310.outs[0]

def generar_reportes(sistema):
    # Reporte de Materia
    materia_data = []
    for s in sistema.streams:
        if s.F_mass > 0.001:
            materia_data.append({
                "Corriente": s.ID,
                "Temp (°C)": round(s.T - 273.15, 2),
                "Flujo Total (kg/h)": round(s.F_mass, 2),
                "Fracción Etanol": round(s.imass['Ethanol']/s.F_mass, 4) if s.F_mass > 0 else 0
            })
    
    # Reporte de Energía
    energia_data = []
    for u in sistema.units:
        duty_kw = 0.0
        # Acceso seguro a la energía térmica
        if hasattr(u, 'heat_utilities') and u.heat_utilities:
            duty_kw = sum([hu.duty for hu in u.heat_utilities]) / 3600
        elif isinstance(u, bst.HXprocess):
            duty_kw = (u.outs[0].H - u.ins[0].H) / 3600
            
        if abs(duty_kw) > 0.001:
            energia_data.append({
                "Equipo": u.ID,
                "Carga Térmica (kW)": round(duty_kw, 2),
                "Tipo": "Calentamiento" if duty_kw > 0 else "Enfriamiento"
            })
            
    return pd.DataFrame(materia_data), pd.DataFrame(energia_data)

# ==========================================
# 3. INTERFAZ DE USUARIO (UI)
# ==========================================
st.title("🧪 IALabs: Simulador BioSTEAM Web")
st.sidebar.header("Panel de Control")

with st.sidebar:
    f_agua = st.slider("Flujo de Agua (kg/h)", 500, 2000, 900)
    f_etanol = st.slider("Flujo de Etanol (kg/h)", 10, 500, 100)
    t_entrada = st.number_input("Temp. Alimentación (°C)", 15, 50, 25)
    
    st.divider()
    usar_ia = st.toggle("Activar Tutor IA (Gemini)")
    btn_simular = st.button("🚀 Iniciar Simulación", use_container_width=True)

if btn_simular:
    try:
        with st.spinner("Ejecutando balance de materia y energía..."):
            sys_sim, prod = run_simulation(f_agua, f_etanol, t_entrada)
            df_m, df_e = generar_reportes(sys_sim)
            
            # --- SECCIÓN DE MÉTRICAS (KPIs) ---
            pureza_final = (prod.imass['Ethanol'] / prod.F_mass) * 100
            c1, c2, c3 = st.columns(3)
            c1.metric("Pureza del Producto", f"{pureza_final:.2f} %")
            c2.metric("Masa de Producto", f"{prod.F_mass:.1f} kg/h")
            c3.metric("Recuperación Etanol", f"{(prod.imass['Ethanol']/f_etanol)*100:.1f} %")

            # --- SECCIÓN DE TABLAS Y DIAGRAMA ---
            t1, t2, t3 = st.tabs(["📋 Materia", "🔥 Energía", "📊 PFD"])
            
            with t1:
                st.subheader("Balance de Masa por Corriente")
                st.dataframe(df_m, use_container_width=True)
            
            with t2:
                st.subheader("Consumo Energético")
                st.table(df_e)
                
            with t3:
                st.subheader("Diagrama de Proceso")
                try:
                    sys_sim.diagram(file="pfd_output", format="png", display=False)
                    st.image("pfd_output.png")
                except:
                    st.warning("Diagrama no disponible. Asegúrate de que 'graphviz' esté instalado en el sistema.")

            # --- INTEGRACIÓN CON IA ---
            if usar_ia:
                st.divider()
                st.subheader("🤖 Análisis del Tutor de Ingeniería")
                api_key = st.secrets.get("GEMINI_API_KEY")
                
                if api_key:
                    genai.configure(api_key=api_key)
                    model = genai.GenerativeModel('gemini-2.5-pro')
                    prompt = f"""Analiza estos resultados de simulación para un estudiante:
                    Entrada: {f_etanol} kg/h etanol, {f_agua} kg/h agua.
                    Salida: {pureza_final:.2f}% pureza.
                    Explica brevemente por qué la pureza aumentó tras el Flash."""
                    
                    response = model.generate_content(prompt)
                    st.info(response.text)
                else:
                    st.error("Error: No se encontró la GEMINI_API_KEY en los Secrets.")

    except Exception as e:
        st.error(f"Error en la simulación: {e}")
else:
    st.write("Configura los flujos en la izquierda y presiona el botón para ver los resultados.")
