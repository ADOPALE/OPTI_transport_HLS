import streamlit as st
from streamlit_option_menu import option_menu
import os

# Import des fonctions modules (vérifiez que vos fichiers ont ces noms dans /modules)
from modules.Import import show_import
#from modules.dataViz import show_volumes, show_biologie
#from modules.paramSim import show_simulation
# Note : Les résultats ne s'affichent que si une condition est remplie

# CONFIGURATION PAGE
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

# GESTION DES CHEMINS LOGOS
curr_dir = os.path.dirname(os.path.abspath(__file__))
logo_adopale = os.path.join(curr_dir, "assets", "ADOPALE.jpeg")
logo_chu = os.path.join(curr_dir, "assets", "logo_CHU.png")

# --- SIDEBAR ---
with st.sidebar:
    # Affichage des logos
    col_l1, col_l2 = st.columns(2)
    with col_l1:
        if os.path.exists(logo_adopale): st.image(logo_adopale, use_container_width=True)
    with col_l2:
        if os.path.exists(logo_chu): st.image(logo_chu, use_container_width=True)
    
    st.divider()

    # MENU DE NAVIGATION
    # On définit les options dynamiquement
    menu_options = ["Accueil", "Importer Données", "Volumes Distribution", "Passages Biologie", "Simuler & Optimiser"]
    
    # Condition pour afficher les résultats (simulé ici par une variable en session_state)
    if st.session_state.get('sim_complete', False):
        menu_options.append("Résultats")
        menu_options.append("Exporter")

    selected = option_menu(
        menu_title=None,
        options=menu_options,
        icons=["house", "cloud-upload", "truck", "microscope", "play-circle", "clipboard-data", "file-earmark-pdf"],
        styles={
            "nav-link": {"color": "black", "text-align": "left", "font-size": "14px"},
            "nav-link-selected": {"background-color": "#e1e4e8", "color": "black", "font-weight": "bold"},
        }
    )

# --- LOGIQUE D'AFFICHAGE ---
if selected == "Accueil":
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("### Bienvenue sur l'outil de simulation ADOPALE x CHU de Nantes")
    st.write("Cet outil permet de modéliser vos tournées et d'optimiser les passages.")
    # Bouton de téléchargement template
    st.download_button("📥 Télécharger le fichier de paramétrage vierge", data="...", file_name="template.xlsx")

elif selected == "Importer Données":
    show_import()

elif selected == "Volumes Distribution":
    show_volumes()

elif selected == "Passages Biologie":
    show_biologie()

elif selected == "Simuler & Optimiser":
    show_simulation()

elif selected == "Résultats":
    st.title("📊 Synthèse des résultats")
    # Appel de la fonction de synthèse
