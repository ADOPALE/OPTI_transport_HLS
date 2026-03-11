import streamlit as st
from streamlit_option_menu import option_menu

st.set_page_config(layout="wide")

# --- BARRE LATÉRALE ---
with st.sidebar:
    st.title("Mon App Logistique")
    
    # Configuration du menu avec icônes (Bootstrap Icons)
    selected = option_menu(
        menu_title="Navigation",  # Titre du menu (ou None pour masquer)
        options=[
            "Accueil", 
            "Paramétrage", 
            "Volumes", 
            "Biologie", 
            "Optimisation"
        ],
        icons=[
            "house",           # Accueil
            "gear",            # Paramétrage
            "box-seam",        # Volumes
            "microscope",      # Biologie
            "command"          # Optimisation
        ], 
        menu_icon="cast",      # Icône du titre du menu
        default_index=0,       # Option sélectionnée par défaut
        styles={
            "container": {"padding": "5!important", "background-color": "#fafafa"},
            "icon": {"color": "orange", "font-size": "20px"}, 
            "nav-link": {
                "font-size": "16px", 
                "text-align": "left", 
                "margin": "0px", 
                "--hover-color": "#eee"
            },
            "nav-link-selected": {"background-color": "#02ab21"}, # Couleur de l'élément actif
        }
    )

# --- LOGIQUE DE NAVIGATION ---
if selected == "Accueil":
    st.title("🏠 Bienvenue")
    st.info("Présentation des fonctionnalités...")

elif selected == "Paramétrage":
    st.title("⚙️ Paramétrage")
    # Votre code d'import ici

elif selected == "Volumes":
    st.title("📦 Analyse des Volumes")

# ... Répétez pour les autres options
