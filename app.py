import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path

from modules.Import import show_import

#__ajout 18/03 LM
from modules.dataViz import show_flux_control_charts
#__fin ajout

#__ajout LM 16/03
from modules.GeoMatrix import run_matrix_tool
#__fin ajout

#__ajout BG 19/03
from modules.biologie_engine import run_optimization
#__ajout BG 19/03

try:
    from modules.dataViz import show_volumes, show_biologie
except ImportError:
    show_volumes = None
    show_biologie = None


st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"

LOGO_ADOPALE = ASSETS_DIR / "ADOPALE.jpg"
LOGO_CHU = ASSETS_DIR / "CHU Nantes.png"
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
    if show_volumes:
        show_volumes()
    else:
        st.warning("Le module de visualisation des volumes n'est pas disponible.")

# Ajout BG 19/03
def show_biologie_page():
    st.title("🧪 Paramétrage des Passages Biologie")

    if "data" not in st.session_state:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
        return

    data = st.session_state["data"]
    df_sites = data["accessibilite_sites"]
    
    # Paramètres globaux
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        duree_max = st.number_input("Durée max tournée (min)", value=200)
    with col_g2:
        temps_coll = st.number_input("Temps de collecte (min)", value=10)

    st.divider()
    st.subheader("🏥 Sélection et configuration des sites")

    # Initialisation du dictionnaire de configuration
    current_sites_config = {}

    for index, row in df_sites.iterrows():
        site_name = row['site']
        if site_name == "HLS": continue 
        
        # Création d'une ligne avec checkbox et expander
        cols = st.columns([1, 4])
        
        # 1. Possibilité de cocher/décocher le site
        is_active = cols[0].checkbox("Inclure", value=True, key=f"check_{site_name}")
        
        if is_active:
            with cols[1].expander(f"📍 {site_name}", expanded=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    # Slider pour la plage horaire
                    # On peut pré-remplir avec les valeurs de l'Excel si vous les avez extraites
                    res = st.select_slider(
                        f"Plage horaire",
                        options=range(0, 1441, 15),
                        value=(480, 1080), # 8h - 18h par défaut
                        format_func=lambda x: f"{x//60:02d}:{x%60:02d}",
                        key=f"slide_{site_name}"
                    )
                with c2:
                    freq = st.number_input(f"Fréquence", min_value=1, value=5, key=f"freq_{site_name}")

                # On n'ajoute à la config que si le site est coché
                current_sites_config[site_name] = {
                    'open': res[0],
                    'close': res[1],
                    'freq': freq
                }
        else:
            cols[1].info(f"❄️ {site_name} est exclu de la simulation.")

    # Sauvegarde
    if st.button("💾 Enregistrer la configuration", use_container_width=True):
        st.session_state["biologie_config"] = {
            "duree_max": duree_max,
            "temps_collecte": temps_coll,
            "sites": current_sites_config
        }
        st.success(f"Configuration enregistrée : {len(current_sites_config)} sites actifs.")
# fin ajout



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
                st.balloons()
                
                # /!\ IMPORTANT : On ne met pas de st.rerun() ici /!\
                # Cela permet de garder l'affichage du succès à l'écran.
                
            except Exception as e:
                st.error(f"Erreur durant le calcul : {e}")

    # 4. ETAT APRES CALCUL
    if st.session_state.get("sim_lancee"):
        st.divider()
        st.markdown("### 📊 Résultats prêts")
        st.info("Vous pouvez maintenant consulter les onglets **Synthèse** et **Détail tournées** pour voir les graphiques et feuilles de route.")


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

# --- Ajout LM 18/03: Contrôle visuel des flux sous l'import ---
    if "data" in st.session_state:
        show_flux_control_charts()
        #__fin ajout
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
