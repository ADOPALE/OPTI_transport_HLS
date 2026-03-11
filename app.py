import streamlit as st
from streamlit_option_menu import option_menu
import os

# --- 1. CONFIGURATION & IMPORTS ---
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

# Initialisation de la variable de simulation dans la mémoire de l'app
if 'sim_lancee' not in st.session_state:
    st.session_state['sim_lancee'] = False

# Import des fonctions modules
from modules.Import import show_import
# Assurez-vous que ces fonctions sont bien définies dans vos fichiers respectifs
#from modules.dataViz import show_volumes, show_biologie
#from modules.paramSim import show_simulation

# --- 2. STYLE CSS (SIDEBAR BLANCHE ET TEXTE GRAS) ---
st.markdown("""
    <style>
        [data-testid="stSidebar"] {
            background-color: white !important;
        }
        [data-testid="stSidebar"] .stText, [data-testid="stSidebar"] p, [data-testid="stSidebar"] h3 {
            color: black !important;
            font-weight: bold !important;
        }
    </style>
""", unsafe_allow_html=True)

# --- 3. GESTION DES LOGOS ---
curr_dir = os.path.dirname(os.path.abspath(__file__))
logo_adopale = os.path.join(curr_dir, "assets", "ADOPALE.png")
logo_chu = os.path.join(curr_dir, "assets", "Logo_CHU.png")

# --- 4. SIDEBAR & NAVIGATION ---
with st.sidebar:
    col_l1, col_l2 = st.columns(2)
    with col_l1:
        if os.path.exists(logo_adopale): st.image(logo_adopale, use_container_width=True)
    with col_l2:
        if os.path.exists(logo_chu): st.image(logo_chu, use_container_width=True)
    
    st.divider()

    # Définition dynamique des options du menu
    options = ["Accueil", "Importer Données", "Volumes Distribution", "Passages Biologie", "Simuler & Optimiser"]
    icons = ["house", "cloud-upload", "truck", "microscope", "play-circle"]

    # Ajout des fenêtres de résultats si la simulation est faite
    if st.session_state['sim_lancee']:
        options.extend(["Synthèse", "Détail tournées", "Exporter"])
        icons.extend(["clipboard-data", "map", "file-earmark-pdf"])

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
            "nav-link-selected": {
                "background-color": "#e1e4e8", 
                "color": "black",
                "font-weight": "900"
            },
        }
    )

# --- 5. LOGIQUE D'AFFICHAGE DES FENÊTRES ---

if selected == "Accueil":
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("### Bienvenue sur l'outil de simulation ADOPALE x CHU de Nantes")
    st.write("Cet outil permet de modéliser vos tournées et d'optimiser les passages.")
    st.download_button("📥 Télécharger le fichier de paramétrage vierge", data="Données de test", file_name="template.xlsx")

elif selected == "Importer Données":
    show_import()

elif selected == "Volumes Distribution":
    show_volumes()

elif selected == "Passages Biologie":
    show_biologie()

elif selected == "Simuler & Optimiser":
    st.title("🏎️ Optimisation")
    # Appel de la fonction du module paramSim
    # On lui passe une fonction ou on gère le bouton ici
    #show_simulation()
    
    st.divider()
    if st.button("🚀 Lancer la simulation définitive"):
        with st.spinner("Calcul des tournées en cours..."):
            # Ici votre logique de calcul (Phase_0.py etc.)
            st.session_state['sim_lancee'] = True
            st.success("Simulation terminée ! Les résultats sont disponibles dans le menu.")
            st.rerun()

elif selected == "Synthèse":
    st.title("📊 Synthèse des résultats")
    # show_results_summary()

elif selected == "Détail tournées":
    st.title("📍 Détail des tournées")
    # show_tournees_detail()

elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
    # show_export_logic()
