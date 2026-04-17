import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
import folium
from streamlit_folium import st_folium
import pandas as pd
import plotly.express as px

# 1. GÉOGRAPHIE & IMPORT
from modules.GeoMatrix import run_matrix_tool
from modules.Import import show_import

# 2. FLUX & DATAVIZ
from modules.check_flux import show_flux_control_charts

# 3. BIOLOGIE
from modules.param_bio import show_biologie_page
from modules.biologie_engine import run_optimization
from modules.resultats_bio import afficher_stats_vehicules, afficher_stats_chauffeurs, afficher_stats_sites, afficher_detail_flotte_vehicules, afficher_detail_itineraire

# 4. TRANSPORT LOURD (DISTRIBUTION)
from modules.param_flux import afficher_parametres_logistique
from modules.simul_flux import MoteurSimulation
from modules.Resultats_simul_flux import afficher_tableau_bord_global, afficher_analyse_operationnelle, afficher_resultats_complets

# --- INITIALISATION ---
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes")

if "active_menu" not in st.session_state:
    st.session_state.active_menu = "Accueil"

# --- SIDEBAR ---
with st.sidebar:
    st.markdown("### 💾 NAVIGATION")
    selected = option_menu(
        menu_title=None,
        options=["Accueil", "Importer Données", "Vérif volumes", "Véhicules et paramètres", "Synthèse transport"],
        icons=["house", "cloud-upload", "bar-chart", "truck", "speedometer2"],
        menu_icon="cast",
        default_index=0,
    )

# --- LOGIQUE D'AFFICHAGE ---
if selected == "Accueil":
    st.title("📍 Optimisation Logistique")
    st.info("Bienvenue. Utilisez le menu à gauche pour commencer.")

elif selected == "Importer Données":
    show_import()

elif selected == "Vérif volumes":
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("Veuillez importer des données d'abord.")

elif selected == "Véhicules et paramètres":
    afficher_parametres_logistique()

elif selected == "Synthèse transport":
    st.title("📊 Synthèse & Simulation")
    if "data" in st.session_state:
        # 1. Bouton de calcul
        if st.button("🚀 Lancer Simulation Transport", type="primary"):
            with st.spinner("Calcul en cours..."):
                params = st.session_state.get("params_logistique", {})
                moteur = MoteurSimulation(st.session_state['data'], params)
                st.session_state['planning_detaille'] = moteur.simuler()
                st.rerun()
        
        # 2. Affichage si résultats présents
        if 'planning_detaille' in st.session_state:
            planning = st.session_state['planning_detaille']
            df_v = st.session_state['data']['param_vehicules']
            df_c = st.session_state['data']['param_contenants']
            
            # Appel de la nouvelle fonction qui affiche tout (Gantt + KPIs + Détails)
            afficher_resultats_complets(planning, df_v, df_c)
    else:
        st.error("⚠️ Aucune donnée chargée. Allez dans l'onglet 'Importer Données'.")
