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
from modules.resultats_bio import (
    afficher_stats_vehicules, 
    afficher_stats_chauffeurs, 
    afficher_stats_sites, 
    afficher_detail_flotte_vehicules, 
    afficher_detail_itineraire
)

# 4. TRANSPORT LOURD (DISTRIBUTION)
from modules.param_flux import afficher_parametres_logistique
from modules.simul_flux import MoteurSimulation
from modules.Resultats_simul_flux import (
    afficher_tableau_bord_global, 
    afficher_analyse_operationnelle, 
    afficher_resultats_complets
)

# --------- DEFINITION DES FONCTIONS NECESSAIRE POUR L'UI ------------
def show_home():
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("---")
    st.markdown("""
    ### Bienvenue sur l'outil de simulation ADOPALE x CHU de Nantes
    Cet outil vous permet de modéliser, visualiser et optimiser vos tournées de distribution et de biologie.

    **Comment procéder ?**
    1. **Téléchargez** le template ci-dessous.
    2. **Remplissez** vos données de sites, de volumes et de fréquences.
    3. **Importez** le fichier dans l'onglet dédié pour lancer vos analyses.
    """)

    # Chemin vers le logo/template (à adapter si besoin)
    TEMPLATE_FILE = Path("assets/Template_vierge.xlsx")
    if TEMPLATE_FILE.exists():
        with open(TEMPLATE_FILE, "rb") as file:
            st.download_button(
                label="📥 Télécharger le fichier de paramétrage vierge",
                data=file,
                file_name="template_parametrage_ADOPALE.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

def show_simulation_page():
    st.title("🏎️ Optimisation des tournées Biologie")
    st.markdown("---")
    if "data" not in st.session_state or "matrice_duree" not in st.session_state["data"]:
        st.error("⚠️ Matrice de durée manquante. Importez vos données d'abord.")
        return
    
    if "biologie_config" not in st.session_state:
        st.warning("⚠️ Configuration manquante. Validez vos paramètres dans l'onglet 'Passages Biologie'.")
        return

    config = st.session_state["biologie_config"]
    btn_label = "🚀 Relancer la simulation" if st.session_state.get("sim_lancee") else "🚀 Lancer la simulation"
    
    if st.button(btn_label, use_container_width=True, type="primary"):
        with st.spinner("🧠 Calcul en cours..."):
            try:
                df_duree = st.session_state["data"]["matrice_duree"]
                resultats = run_optimization(
                    m_duree_df=df_duree,
                    sites_config=config["sites"],
                    temps_collecte=config["temps_collecte"],
                    max_tournee=config["duree_max"],
                    souplesse=st.session_state.get("souplesse_fusion", False)
                )
                st.session_state.resultat_flotte = resultats
                st.session_state.sim_lancee = True
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

# ------------ INITIALISATION DU VISUEL ------------
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

if "sim_lancee" not in st.session_state:
    st.session_state.sim_lancee = False

with st.sidebar:
    st.markdown("### 💾 DONNÉES DE BASE")
    selected = option_menu(
        menu_title=None,
        options=["Accueil", "Outil calcul matrices", "Importer Données", "Vérif volumes à distribuer", 
                 "Paramétrage BIO", "Simul tournées BIO", "Synthèse BIO", "Détail tournées BIO",
                 "Véhicules et paramètres", "Synthèse transport"],
        icons=["house", "grid", "cloud-upload", "bar-chart", "gear", "play", "graph-up", "list-check", "truck", "speedometer2"],
        default_index=0,
    )

# --- LOGIQUE D'AFFICHAGE ---
if selected == "Accueil":
    show_home()
elif selected == "Outil calcul matrices":
    run_matrix_tool()
elif selected == "Importer Données":
    show_import()
elif selected == "Vérif volumes à distribuer":
    st.title("📦 Contrôle des volumes")
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("Importez des données d'abord.")
elif selected == "Véhicules et paramètres":
    afficher_parametres_logistique()
elif selected == "Synthèse transport":
    st.title("📊 Synthèse transport")
    if 'data' not in st.session_state:
        st.error("Importez des données d'abord.")
    else:
        if st.button("🚀 Lancer la simulation transport", type="primary"):
            with st.spinner("Calcul..."):
                moteur = MoteurSimulation(st.session_state['data'], st.session_state.get("params_logistique", {}))
                st.session_state['planning_detaille'] = moteur.simuler()
                st.rerun()
        
        if 'planning_detaille' in st.session_state:
            df_v = st.session_state['data']['param_vehicules']
            df_c = st.session_state['data']['param_contenants']
            afficher_resultats_complets(st.session_state['planning_detaille'], df_v, df_c)
