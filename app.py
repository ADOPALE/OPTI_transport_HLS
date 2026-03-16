import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path

from modules.Import import show_import

#__ajout LM 16/03
from modules.GeoMatrix import run_matrix_tool
#__fin ajout

try:
    from modules.dataViz import show_volumes, show_biologie
except ImportError:
    show_volumes = None
    show_biologie = None


st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

LOGO_ADOPALE = ASSETS_DIR / "ADOPALE.png"
LOGO_CHU = ASSETS_DIR / "Logo_CHU.png"
TEMPLATE_FILE = ASSETS_DIR / "Template_vierge.xlsx"

if "sim_lancee" not in st.session_state:
    st.session_state.sim_lancee = False


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

    if TEMPLATE_FILE.exists():
        with open(TEMPLATE_FILE, "rb") as file:
            st.download_button(
                label="📥 Télécharger le fichier de paramétrage vierge",
                data=file,
                file_name="template_parametrage_ADOPALE.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.error("Le fichier template est introuvable.")

    st.info("💡 Une fois le fichier rempli, rendez-vous dans le menu 'Importer Données'.")


def show_volumes_page():
    if "data" in st.session_state:
        if show_volumes:
            show_volumes()
        else:
            st.warning("Le module de visualisation des volumes n'est pas disponible.")
    else:
        st.error("⚠️ Aucune donnée disponible. Veuillez d'abord importer votre fichier Excel dans l'onglet 'Importer Données'.")

def show_biologie_page():
    if "data" in st.session_state:
        if show_biologie:
            show_biologie()
        else:
            st.warning("Le module biologie n'est pas disponible.")
    else:
        st.error("⚠️ Aucune donnée disponible. Veuillez d'abord importer votre fichier Excel dans l'onglet 'Importer Données'.")

def show_simulation_page():
    st.title("🏎️ Optimisation")
    if "data" in st.session_state:
        if st.button("🚀 Lancer la simulation"):
            st.session_state.sim_lancee = True
            st.rerun()
    else:
        st.error("⚠️ Impossible de lancer la simulation sans données. Importez le fichier Excel de paramétrage.")


with st.sidebar:
    col1, col2 = st.columns(2)
    with col1:
        if LOGO_ADOPALE.exists():
            st.image(str(LOGO_ADOPALE), use_container_width=True)
    with col2:
        if LOGO_CHU.exists():
            st.image(str(LOGO_CHU), use_container_width=True)

    st.divider()
#__ajout LM 16/03 "Calcul Matrices" et "geo-alt"
    options = ["Accueil", "Calcul Matrices", "Importer Données", "Volumes Distribution", "Passages Biologie", "Simuler & Optimiser"]
    icons = ["house", "geo-alt", "cloud-upload", "truck", "microscope", "play-circle"]

    if st.session_state.sim_lancee:
        options += ["Synthèse", "Détail tournées", "Exporter"]
        icons += ["clipboard-data", "map", "file-earmark-pdf"]

    selected = option_menu(
        menu_title=None,
        options=options,
        icons=icons,
        styles={
            "container": {"background-color": "white", "border-radius": "0"},
            "icon": {"color": "black", "font-size": "18px"},
            "nav-link": {
                "color": "black",
                "font-size": "15px",
                "font-weight": "bold",
                "text-align": "left",
                "margin": "5px",
                "--hover-color": "#f0f2f6"
            },
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
    #__ajout LM 16/03 
elif selected == "Calcul Matrices":
    run_matrix_tool()
    #fin ajout
elif selected == "Importer Données":
    show_import()
elif selected == "Volumes Distribution":
    show_volumes_page()
elif selected == "Passages Biologie":
    show_biologie_page()
elif selected == "Simuler & Optimiser":
    show_simulation_page()
elif selected == "Synthèse":
    st.title("📊 Synthèse des résultats")
elif selected == "Détail tournées":
    st.title("📍 Détail des tournées")
elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
