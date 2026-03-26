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
from modules.resultats_bio import afficher_stats_vehicules, afficher_stats_chauffeurs, afficher_stats_sites, afficher_detail_flotte_vehicules, afficher_detail_itineraire

# importer la fonction qui permet de paramétrer les tournées de  camions
from modules.param_flux import afficher_parametres_logistique



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

    # 1. VERIFICATIONS
    if "data" not in st.session_state or "matrice_duree" not in st.session_state["data"]:
        st.error("⚠️ Matrice de durée manquante. Importez vos données d'abord.")
        return

    if "biologie_config" not in st.session_state:
        st.warning("⚠️ Configuration manquante. Validez vos paramètres dans l'onglet 'Passages Biologie'.")
        return

    config = st.session_state["biologie_config"]
    st.info(f"Prêt à simuler {len(config['sites'])} sites hospitaliers.")

    # 2. LE BOUTON
    # Si la simulation est déjà lancée, on peut afficher un message ou griser le bouton
    btn_label = "🚀 Relancer la simulation" if st.session_state.get("sim_lancee") else "🚀 Lancer la simulation"
    
    if st.button(btn_label, use_container_width=True, type="primary"):
        with st.spinner("🧠 Calcul de l'itinéraire optimal en cours..."):
            try:
                df_duree = st.session_state["data"]["matrice_duree"]
                
                resultats = run_optimization(
                    m_duree_df=df_duree,
                    sites_config=config["sites"],
                    temps_collecte=config["temps_collecte"],
                    max_tournee=config["duree_max"]
                )
                
                # STOCKAGE DES RESULTATS
                st.session_state.resultat_flotte = resultats
                st.session_state.sim_lancee = True

                # --- LE RERUN DOIT ETRE ICI ---
                # Il force Streamlit à relire tout le script. 
                # Au prochain passage, il verra 'sim_lancee = True' dès le début.
                st.rerun()
                
            except Exception as e:
                st.error(f"Erreur durant le calcul : {e}")

    # 3. AFFICHAGE DU SUCCÈS (Hors du bloc bouton)
    # Ce bloc s'exécutera immédiatement après le st.rerun()
    if st.session_state.get("sim_lancee"):
        st.success(f"✅ Simulation réussie ! {len(st.session_state.resultat_flotte)} véhicules identifiés.")
        st.divider()
        st.markdown("### 📊 Résultats prêts")
        st.info("Vous pouvez maintenant consulter les onglets **Synthèse** et **Détail tournées** dans le menu de gauche.")





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
    # 1. Logos
    col1, col2 = st.columns(2)
    with col1:
        if LOGO_ADOPALE.exists():
            st.image(str(LOGO_ADOPALE), use_container_width=True)
    with col2:
        if LOGO_CHU.exists():
            st.image(str(LOGO_CHU), use_container_width=True)

    st.divider()
    #st.title("Logistique CHU")

    # --- Styles communs pour uniformiser les 3 menus ---
    menu_styles = {
        "container": {"background-color": "white", "padding": "0", "border-radius": "0"},
        "icon": {"color": "#00558E", "font-size": "18px"},
        "nav-link": {"color": "black", "font-size": "14px", "font-weight": "bold", "text-align": "left", "margin": "0px"},
        "nav-link-selected": {"background-color": "#e1e4e8", "color": "black", "font-weight": "900"},
    }

    # --- GROUPE 1 : DONNÉES DE BASE ---
    st.markdown("### 💾 DONNÉES DE BASE")
    sel_data = option_menu(
        menu_title=None,
        options=["Accueil", "Outil calcul matrices", "Importer Données"],
        icons=["house-door", "grid-3x3-gap", "file-earmark-arrow-up"],
        styles=menu_styles,
        key="menu_data"
    )

    # --- GROUPE 2 : BIOLOGIE ---
    st.markdown("### 🧪 BIOLOGIE")
    sel_bio = option_menu(
        menu_title=None,
        options=["Paramétrage BIO", "Simul tournées BIO", "Synthèse BIO", "Détail tournées BIO"],
        icons=["gear-wide-connected", "play-btn", "clipboard2-pulse", "signpost-split"],
        styles=menu_styles,
        key="menu_bio"
    )

    # --- GROUPE 3 : TRANSPORT LOURD ---
    st.markdown("### 🚚 DISTRIBUTION")
    sel_bio = option_menu(
        menu_title=None,
        options=["Vérif volumes à distribuer", "Véhicules et paramètres", "Simul tournées", "Synthèse transport", "Détail tournées"],
        icons=["bar-chart-steps", "truck-front", "play-btn", "clipboard2-pulse", "signpost-split"],
        styles=menu_styles,
        key="menu_distrib"
    )
    
    # --- GROUPE 4 : EXPORT ---
    st.markdown("### 📤 SORTIES")
    sel_export = option_menu(
        menu_title=None,
        options=["Exporter"],
        icons=["download"],
        styles=menu_styles,
        key="menu_export"
    )

    # --- LOGIQUE DE SYNCHRONISATION ---
    # Ce bloc est crucial : il détecte quel menu a été cliqué en dernier 
    # et met à jour la variable 'selected' unique pour le reste de app.py
    
    if "active_menu" not in st.session_state:
        st.session_state.active_menu = "Accueil"

    # Détection du changement dans chaque menu
    if st.session_state.menu_data != st.session_state.get('prev_data'):
        st.session_state.active_menu = st.session_state.menu_data
        st.session_state.prev_data = st.session_state.menu_data
        
    if st.session_state.menu_bio != st.session_state.get('prev_bio'):
        st.session_state.active_menu = st.session_state.menu_bio
        st.session_state.prev_bio = st.session_state.menu_bio

    if st.session_state.menu_distrib != st.session_state.get('prev_distrib'):
        st.session_state.active_menu = st.session_state.menu_distrib
        st.session_state.prev_distrib = st.session_state.menu_distrib
        
    if st.session_state.menu_export != st.session_state.get('prev_export'):
        st.session_state.active_menu = st.session_state.menu_export
        st.session_state.prev_export = st.session_state.menu_export

    # C'est cette variable que votre app.py utilisera
    selected = st.session_state.active_menu


if selected == "Accueil":
    show_home()
    
elif selected == "Outil calcul matrices":
    run_matrix_tool()
    
elif selected == "Importer Données":
    show_import()
    
elif selected == "Vérif volumes à distribuer":
    st.title("📦 Contrôle des volumes à transporter")
    if "data" in st.session_state:
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel dans l'onglet 'Importer Données'.")
        #__fin ajout
elif selected == "Paramétrage BIO":
    show_biologie_page()
    
elif selected == "Simul tournées BIO":
    show_simulation_page()

elif selected == "Synthèse BIO":
    st.title("📊 Synthèse des résultats")
    if not st.session_state.get("sim_lancee"):
        st.info("💡 Les résultats s'afficheront ici une fois la simulation lancée dans l'onglet **'Simuler & Optimiser'**.")
    else:
        # Code d'affichage normal
        resultats = st.session_state.resultat_flotte
        df_dist = st.session_state["data"]["matrice_distance"]
        config_rh = st.session_state["biologie_config"]["rh"]
        
        afficher_stats_vehicules(resultats, df_dist)
        st.divider()
        afficher_stats_chauffeurs(resultats, config_rh)
        st.divider()
        afficher_stats_sites(resultats)

elif selected == "Détail tournées BIO":
    st.title("📋 Détail des tournées")
    if not st.session_state.get("sim_lancee"):
        st.info("💡 Veuillez lancer la simulation pour visualiser le détail des tournées et les cartes.")
    
    else:
        res = st.session_state.resultat_flotte
        df_dist = st.session_state["data"]["matrice_distance"]
        df_adresses = st.session_state["data"].get("adresses", st.session_state["data"].get("df_sites"))
        
        if df_adresses is not None:
            # Préparation du dictionnaire
            sites_adresses = pd.Series(df_adresses.adresse.values, index=df_adresses.site.str.upper()).to_dict()
            hls_adresse = sites_adresses.get("HLS", "55 Boulevard Gustave Roch, 44000 Nantes")
            
            v_sel, vac_sel = afficher_detail_flotte_vehicules(res, df_dist)
            
            if v_sel:
                # L'appel à la fonction
                afficher_detail_itineraire(v_sel, vac_sel, sites_adresses, hls_adresse)

elif selected == "Véhicules et paramètres":
    afficher_parametres_logistique()


elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
