import streamlit as st
from streamlit_option_menu import option_menu

# Configuration de la page
st.set_page_config(layout="wide", page_title="Expert Logistique & Bio")

# --- BARRE LATÉRALE ---
import streamlit as st
from streamlit_option_menu import option_menu

with st.sidebar:
    st.markdown("### 🚀 Menu")
    selected = option_menu(
        menu_title=None,
        options=[
            "Accueil", 
            "Paramétrage", 
            "Volumes Distribution", 
            "Biologie", 
            "Optimisation"
        ],
        # On intègre l'icône directement dans le texte pour un rendu garanti
        icons=["house", "gear", "box", "microscope", "map"], 
        styles={
            "container": {"background-color": "white"},
            "icon": {"color": "black", "font-size": "18px"},
            "nav-link": {
                "color": "black", 
                "font-size": "15px", 
                "text-align": "left",
                "--hover-color": "#eee"
            },
            "nav-link-selected": {"background-color": "#e1e4e8", "color": "black"}
        }
    )

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
