import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path

from modules.Import import show_import

#__ajout 18/03 LM
from modules.dataViz import show_flux_control_charts
#__fin ajout

#__ajout LM 16/03
from modules.GeoMatrix import run_matrix_tool
#__fin ajout

#__ajout BG 19/03
from modules.biologie_engine import run_optimization
import folium
from streamlit_folium import st_folium
import pandas as pd
import plotly.express as px
#__ajout BG 19/03

# --- NOUVEAUX IMPORTS VISUALISATION ---
from modules.bioViz import calculate_kpis, render_fleet_gantt, render_site_passages, render_tournee_map
# --------------------------------------

try:
    from modules.dataViz import show_volumes, show_biologie
except ImportError:
    show_volumes = None
    show_biologie = None

st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

LOGO_ADOPALE = ASSETS_DIR / "ADOPALE.jpg"
LOGO_CHU = ASSETS_DIR / "CHU Nantes.png"
TEMPLATE_FILE = ASSETS_DIR / "Template_vierge.xlsx"

if "sim_lancee" not in st.session_state:
    st.session_state.sim_lancee = False

def show_home():
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("---")
    st.markdown("""
    ### Bienvenue sur l'outil de simulation ADOPALE x CHU de Nantes
    Cet outil vous permet de modéliser, visualiser et optimiser les flux de transport.
    """)

with st.sidebar:
    # Gestion du logo
    if LOGO_ADOPALE.exists():
        st.image(str(LOGO_ADOPALE), width=200)
    else:
        st.title("ADOPALE")
    
    st.write("---")
    
    selected = option_menu(
        menu_title="Menu Principal",
        options=["Accueil", "Importer Données", "Volumes Distribution", "Calcul Matrices", "🧪 Passages Biologie", "Simuler & Optimiser"],
        icons=["house", "cloud-upload", "bar-chart", "geo-alt", "thermometer-half", "play-circle"],
        menu_icon="cast",
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "#f8f9fa"},
            "nav-link-selected": {"background-color": "#e1e4e8", "color": "black", "font-weight": "900"},
        }
    )

    if st.session_state.sim_lancee:
        st.markdown("---")
        if st.button("🔄 Réinitialiser la simulation", use_container_width=True):
            st.session_state.sim_lancee = False
            st.rerun()

if selected == "Accueil":
    show_home()
elif selected == "Calcul Matrices":
    run_matrix_tool()
elif selected == "Importer Données":
    show_import()
elif selected == "Volumes Distribution":
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
elif selected == "🧪 Passages Biologie":
    from modules.bioViz import show_sites_on_map
    show_sites_on_map()
elif selected == "Simuler & Optimiser":
    st.header("🚀 Optimisation des tournées Biologie")
    
    if "data" not in st.session_state:
        st.error("Veuillez importer des données avant de simuler.")
    else:
        # --- SECTION CONFIGURATION ---
        with st.expander("⚙️ Paramètres de simulation", expanded=not st.session_state.sim_lancee):
            c1, c2 = st.columns(2)
            with c1:
                amplitude = st.number_input("Amplitude poste (min)", value=450)
                pause = st.number_input("Pause (min)", value=30)
                releve = st.number_input("Relève véhicule (min)", value=15)
            with c2:
                max_t = st.slider("Durée max tournée (min)", 30, 240, 120)
                t_coll = st.slider("Temps collecte (min)", 2, 20, 10)

        if st.button("Lancer l'optimisation", use_container_width=True):
            # Préparation simplifiée des sites pour le moteur
            sites_config = {}
            df_flux = st.session_state["data"]["m_flux"]
            for s in df_flux["Site hospitalier"].unique():
                sites_config[s] = {'open': 480, 'close': 1140, 'freq': 3}
            
            config_rh = {'amplitude': amplitude, 'pause': pause, 'releve': releve}
            
            res = run_optimization(st.session_state["data"]["matrice_duree"], sites_config, t_coll, max_t, config_rh)
            st.session_state.resultat_flotte = res
            st.session_state.sim_lancee = True
            st.rerun()

        # --- AJOUTS DEMANDÉS : VISUALISATION ---
        if st.session_state.sim_lancee:
            flotte = st.session_state.resultat_flotte
            config_rh = {'amplitude': amplitude, 'pause': pause, 'releve': releve}
            df_dist = st.session_state["data"]["matrice_distance"]
            df_geo = st.session_state["data"]["accessibilite_sites"] # Pour les coordonnées

            tab_synth, tab_det = st.tabs(["📊 Synthèse", "🚐 Détail des tournées"])

            with tab_synth:
                kpis = calculate_kpis(flotte, config_rh, df_dist)
                col1, col2, col3, col4, col5 = st.columns(5)
                col1.metric("Véhicules", kpis["v_total"])
                col2.metric("Chauffeurs", kpis["c_total"])
                col3.metric("Occupation", f"{kpis['tx_occ']:.1f}%")
                col4.metric("Tournées", kpis["t_total"])
                col5.metric("Km Total", f"{int(kpis['km_total'])} km")

                st.subheader("Histogramme d'occupation de la flotte")
                render_fleet_gantt(flotte)
                
                st.subheader("Passages par site")
                render_site_passages(flotte)

            with tab_det:
                v_sel = st.selectbox("Sélectionner un véhicule", list(flotte.keys()))
                v_data = flotte[v_sel]
                
                # Stats du véhicule
                v_nb_t = sum(len(p) for p in v_data)
                st.write(f"**Nombre de tournées :** {v_nb_t}")
                render_fleet_gantt(flotte, v_highlight=v_sel)

                st.divider()
                
                # Choix tournée
                t_list = []
                for p_idx, p in enumerate(v_data):
                    for t_idx, t in enumerate(p):
                        t_list.append({"label": f"Chauffeur {p_idx+1} - Tournée {t_idx+1}", "data": t})
                
                sel_t = st.selectbox("Sélectionner une tournée", t_list, format_func=lambda x: x["label"])
                
                cm, ct = st.columns([2, 1])
                with cm:
                    render_tournee_map(sel_t["data"], df_geo)
                with ct:
                    st.write("**Passages**")
                    st.table(pd.DataFrame(sel_t["data"]))
