import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
from streamlit_folium import st_folium
import folium
import pandas as pd
import plotly.express as px


# importer la fonction d'encodage géographique + calculer les matrices distance et de durée
from modules.GeoMatrix import run_matrix_tool

# importer la fonction d'import du fichier de paramétrage
from modules.Import import show_import
# importer la fonction pour afficher les flux par fonction support pour contrôle
from modules.check_flux import show_flux_control_charts

# importer la fonction qui permet de paramétrer les tournées de biologie
from modules.param_bio import show_biologie_page
# importer la fonction qui calcule les tournées de biologie
from modules.biologie_engine import run_optimization
# importer les fonctions qui permettent de visualiser les tournées calculées de biologie dans les onglets synthèse et détail des tournées. 
# ____ fonction en cours de travail.
from resultats_bio import afficher_stats_vehicules





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
    if show_volumes:
        show_volumes()
    else:
        st.warning("Le module de visualisation des volumes n'est pas disponible.")


def show_simulation_page():
    st.title("🏎️ Optimisation des tournées Biologie")
    st.markdown("---")

    # 1. VERIFICATIONS (Sécurité)
    if "data" not in st.session_state or "matrice_duree" not in st.session_state["data"]:
        st.error("⚠️ Matrice de durée manquante. Importez vos données d'abord.")
        return

    if "biologie_config" not in st.session_state:
        st.warning("⚠️ Configuration manquante. Validez vos paramètres dans l'onglet 'Passages Biologie'.")
        return

    # 2. RESUME DE CE QUI VA ETRE CALCULE
    config = st.session_state["biologie_config"]
    st.info(f"Prêt à simuler {len(config['sites'])} sites hospitaliers.")

    # 3. LE BOUTON (Unique déclencheur)
    # On n'exécute le code QUE si l'utilisateur clique. 
    if st.button("🚀 Lancer la simulation", use_container_width=True, type="primary"):
        
        with st.spinner("🧠 Calcul de l'itinéraire optimal en cours..."):
            try:
                # Récupération de la matrice
                df_duree = st.session_state["data"]["matrice_duree"]
                
                # Appel du moteur (Partie 1 que tu as déjà dans ton module)
                resultats = run_optimization(
                    m_duree_df=df_duree,
                    sites_config=config["sites"],
                    temps_collecte=config["temps_collecte"],
                    max_tournee=config["duree_max"]
                )
                
                # ON STOCKAGE DES RESULTATS
                st.session_state.resultat_flotte = resultats
                st.session_state.sim_lancee = True
                
                # Succès visuel
                st.success(f"✅ Simulation réussie ! {len(resultats)} véhicules identifiés.")
                
                # /!\ IMPORTANT : On ne met pas de st.rerun() ici /!\
                # Cela permet de garder l'affichage du succès à l'écran.
                
            except Exception as e:
                st.error(f"Erreur durant le calcul : {e}")

    # 4. ETAT APRES CALCUL
    if st.session_state.get("sim_lancee"):
        st.divider()
        st.markdown("### 📊 Résultats prêts")
        st.info("Vous pouvez maintenant consulter les onglets **Synthèse** et **Détail tournées** pour voir les graphiques et feuilles de route.")








# ------------ INITIALISATION DU VISUEL DE L'APPLICATION---------------

st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

LOGO_ADOPALE = ASSETS_DIR / "ADOPALE.jpg"
LOGO_CHU = ASSETS_DIR / "CHU Nantes.png"
TEMPLATE_FILE = ASSETS_DIR / "Template_vierge.xlsx"

if "sim_lancee" not in st.session_state:
    st.session_state.sim_lancee = False

with st.sidebar:
    col1, col2 = st.columns(2)
    with col1:
        if LOGO_ADOPALE.exists():
            st.image(str(LOGO_ADOPALE), use_container_width=True)
    with col2:
        if LOGO_CHU.exists():
            st.image(str(LOGO_CHU), use_container_width=True)

    st.divider()
    options = ["Accueil", "Calcul Matrices", "Importer Données", "Volumes Distribution", "🧪 Passages Biologie", "Simuler & Optimiser"]
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
elif selected == "Calcul Matrices":
    run_matrix_tool()
elif selected == "Importer Données":
    show_import()
elif selected == "Volumes Distribution":
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel dans l'onglet 'Importer Données'.")
        #__fin ajout
elif selected == "🧪 Passages Biologie":
    show_biologie_page()
elif selected == "Simuler & Optimiser":
    show_simulation_page()
elif selected == "Synthèse":
    st.title("📊 Synthèse des résultats")
elif selected == "Détail tournées":
    st.title("📊 Détail des tournées")
elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
