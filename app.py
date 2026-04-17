import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
import folium
from streamlit_folium import st_folium
import pandas as pd
import plotly.express as px

# --- 1. IMPORTS DES MODULES ---
from modules.GeoMatrix import run_matrix_tool
from modules.Import import show_import
from modules.check_flux import show_flux_control_charts

# BIOLOGIE
from modules.param_bio import show_biologie_page
from modules.biologie_engine import run_optimization
from modules.resultats_bio import (
    afficher_stats_vehicules, 
    afficher_stats_chauffeurs, 
    afficher_stats_sites, 
    afficher_detail_flotte_vehicules, 
    afficher_detail_itineraire
)

# DISTRIBUTION (TRANSPORT LOURD)
from modules.param_flux import afficher_parametres_logistique
from modules.simul_flux import MoteurSimulation
from modules.Resultats_simul_flux import (
    afficher_tableau_bord_global, 
    afficher_analyse_operationnelle, 
    afficher_resultats_complets
)

# --- 2. CONFIGURATION & NAVIGATION ---
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes")

with st.sidebar:
    st.title("🚚 Outil Logistique")
    selected = option_menu(
        menu_title="Menu Principal",
        options=[
            "Accueil", 
            "Importer Données", 
            "Vérif volumes", 
            "Paramétrage BIO", 
            "Simulation BIO", 
            "Résultats BIO",
            "Paramètres TRANSPORT", 
            "Simulation TRANSPORT"
        ],
        icons=["house", "cloud-upload", "bar-chart", "gear", "play-circle", "graph-up", "truck", "speedometer2"],
        default_index=0,
    )

# --- 3. LOGIQUE DES PAGES ---

if selected == "Accueil":
    st.title("📍 Optimisation des flux - CHU de Nantes")
    st.markdown("""
    Bienvenue dans l'outil d'aide à la décision pour la logistique.
    * **Biologie** : Optimisation des tournées de prélèvements.
    * **Transport** : Simulation de la distribution lourde (Multi-Quais).
    """)

elif selected == "Importer Données":
    show_import()
    run_matrix_tool()

elif selected == "Vérif volumes":
    st.title("📦 Contrôle des volumes (DataViz)")
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez importer un fichier Excel.")

elif selected == "Paramétrage BIO":
    show_biologie_page()

elif selected == "Simulation BIO":
    st.title("🧪 Simulation Biologie")
    if "biologie_config" in st.session_state:
        if st.button("🚀 Lancer la simulation Bio", type="primary"):
            with st.spinner("Calcul..."):
                config = st.session_state["biologie_config"]
                res = run_optimization(
                    st.session_state["data"]["matrice_duree"],
                    config["sites"],
                    config["temps_collecte"],
                    config["duree_max"]
                )
                st.session_state.resultat_flotte = res
                st.success("Simulation terminée !")
    else:
        st.error("Configurez la biologie d'abord.")

elif selected == "Résultats BIO":
    if "resultat_flotte" in st.session_state:
        tab1, tab2, tab3 = st.tabs(["Synthèse", "Détail Véhicules", "Itinéraires"])
        with tab1:
            afficher_stats_vehicules(st.session_state.resultat_flotte)
        with tab2:
            afficher_detail_flotte_vehicules(st.session_state.resultat_flotte)
        with tab3:
            afficher_detail_itineraire(st.session_state.resultat_flotte)
    else:
        st.info("Lancez la simulation Bio pour voir les résultats.")

elif selected == "Paramètres TRANSPORT":
    afficher_parametres_logistique()

elif selected == "Simulation TRANSPORT":
    st.title("📊 Simulation Distribution")
    if "data" in st.session_state:
        if st.button("🚀 Lancer Simulation Transport", type="primary"):
            params = st.session_state.get("params_logistique", {})
            moteur = MoteurSimulation(st.session_state['data'], params)
            st.session_state['planning_detaille'] = moteur.simuler()
            st.rerun()
        
        if 'planning_detaille' in st.session_state:
            df_v = st.session_state['data']['param_vehicules']
            df_c = st.session_state['data']['param_contenants']
            afficher_resultats_complets(st.session_state['planning_detaille'], df_v, df_c)
