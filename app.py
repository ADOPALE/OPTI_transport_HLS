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


# --- INJECTION CSS POUR FORCE LE BLANC SUR TOUTE LA SIDEBAR ---
st.markdown("""
    <style>
        /* Force le fond de la sidebar en blanc */
        [data-testid="stSidebar"] {
            background-color: white !important;
        }
        /* Ajuste la ligne de séparation si nécessaire */
        [data-testid="stSidebarNav"] {
            background-color: white !important;
        }
    </style>
""", unsafe_allow_html=True)


# GESTION DES CHEMINS LOGOS
curr_dir = os.path.dirname(os.path.abspath(__file__))
logo_adopale = os.path.join(curr_dir, "assets", "ADOPALE.jpeg")
logo_chu = os.path.join(curr_dir, "assets", "Logo_CHU.png")


# --- SIDEBAR ---
with st.sidebar:
    if os.path.exists(logo_adopale):
        st.image(logo_adopale, width=150)
    
    st.divider()

    # MENU DE NAVIGATION
    selected = option_menu(
        menu_title=None,
        options=["Accueil", "Importer Données", "Volumes", "Biologie", "Optimisation"],
        icons=["house", "cloud-upload", "truck", "microscope", "play-circle"],
        styles={
            "container": {
                "padding": "0!important", 
                "background-color": "white", # Fond du menu en blanc
                "border-radius": "0"
            },
            "icon": {"color": "black", "font-size": "18px"}, 
            "nav-link": {
                "color": "black", 
                "font-size": "15px", 
                "text-align": "left", 
                "margin": "5px", 
                "--hover-color": "#f0f2f6" # Gris très léger au survol
            },
            "nav-link-selected": {
                "background-color": "#e1e4e8", # Gris clair pour l'onglet actif
                "color": "black",
                "font-weight": "bold"
            },
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
