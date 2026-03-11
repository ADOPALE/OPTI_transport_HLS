import streamlit as st
from streamlit_option_menu import option_menu

# Configuration de la page
st.set_page_config(layout="wide", page_title="Expert Logistique & Bio")

# --- BARRE LATÉRALE ---
with st.sidebar:
    st.markdown("### 🚀 Menu Principal")
    
    selected = option_menu(
        menu_title=None, # Pas de titre interne pour un look plus épuré
        options=[
            "Accueil", 
            "Paramétrage", 
            "Volumes Distribution", 
            "Biologie", 
            "Optimisation"
        ],
        # Sélection d'icônes Bootstrap cohérentes
        icons=[
            "house",          # Accueil
            "gear-fill",      # Paramétrage
            "box-seam",       # Volumes
            "microscope",           # Biologie (Icône de tube à essai)
            "diagram-3"       # Optimisation (Icône de réseau/tournées)
        ], 
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "#ffffff"},
            "icon": {"color": "#444444", "font-size": "18px"}, 
            "nav-link": {
                "font-size": "15px", 
                "text-align": "left", 
                "margin": "5px", 
                "color": "black",          # <<-- Texte en noir ici
                "font-weight": "500",      # Un peu plus épais pour la lisibilité
                "--hover-color": "#f0f2f6" # Gris très léger au survol
            },
            "nav-link-selected": {
                "background-color": "#e1e4e8", # Fond gris clair pour l'élément actif
                "color": "black",              # Texte reste noir quand sélectionné
                "font-weight": "bold"
            },
        }
    )
    st.divider()

# --- LOGIQUE D'AFFICHAGE (CONTENU) ---
if selected == "Accueil":
    st.title("🏠 Écran d'Accueil")
    # ... votre contenu d'accueil

elif selected == "Paramétrage":
    st.title("⚙️ Paramétrage des données")

elif selected == "Volumes Distribution":
    st.title("📦 Volumes de distribution")

elif selected == "Biologie":
    st.title("🔬 Passages de Biologie")
    st.info("Visualisation des flux biologiques et prélèvements.")

elif selected == "Optimisation":
    st.title("🏎️ Optimisation des Tournées")
