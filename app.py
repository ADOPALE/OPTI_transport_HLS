import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
import pandas as pd

# --- IMPORTS DES MODULES (Structure existante) ---
from modules.Import import show_import
from modules.dataViz import show_flux_control_charts
from modules.GeoMatrix import run_matrix_tool
from modules.biologie_engine import run_optimization
# On importe les nouvelles fonctions de visualisation de bioViz
from modules.bioViz import calculate_kpis, render_fleet_gantt, render_site_passages, render_tournee_map

# --- CONFIGURATION PAGE ---
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE", page_icon="📍")

# --- GESTION DES ASSETS ---
BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
# Utilisation de try/except pour éviter le crash si l'image manque
try:
    LOGO_ADOPALE = str(ASSETS_DIR / "ADOPALE.jpg")
except:
    LOGO_ADOPALE = None

# --- INITIALISATION SESSION STATE ---
if "sim_lancee" not in st.session_state:
    st.session_state.sim_lancee = False
if "data" not in st.session_state:
    st.session_state["data"] = None

# --- SIDEBAR NAVIGATION ---
with st.sidebar:
    if LOGO_ADOPALE:
        st.image(LOGO_ADOPALE, width=200)
    else:
        st.title("ADOPALE")
    
    st.write("---")
    
    selected = option_menu(
        menu_title="Navigation",
        options=["Accueil", "Importer Données", "Volumes Distribution", "Calcul Matrices", "🧪 Passages Biologie", "Simuler & Optimiser"],
        icons=["house", "cloud-upload", "bar-chart", "geo-alt", "thermometer-half", "play-circle"],
        menu_icon="cast",
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "#f8f9fa"},
            "nav-link-selected": {"background-color": "#003399", "color": "white"},
        }
    )

    if st.session_state.sim_lancee:
        st.write("---")
        if st.button("🔄 Réinitialiser la simulation", use_container_width=True):
            st.session_state.sim_lancee = False
            st.session_state.resultat_flotte = None
            st.rerun()

# --- LOGIQUE DES PAGES ---

if selected == "Accueil":
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("---")
    st.info("Bienvenue. Veuillez charger vos données dans l'onglet 'Importer Données' pour commencer.")

elif selected == "Importer Données":
    show_import()

elif selected == "Volumes Distribution":
    if st.session_state["data"]:
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")

elif selected == "Calcul Matrices":
    run_matrix_tool()

elif selected == "🧪 Passages Biologie":
    # Utilise votre fonction de bioViz.py pour la carte des sites
    from modules.bioViz import show_sites_on_map
    show_sites_on_map()

elif selected == "Simuler & Optimiser":
    st.header("🚀 Optimisation des tournées Biologie")
    
    if not st.session_state["data"]:
        st.error("Données manquantes. Veuillez passer par l'étape 'Importer Données'.")
    else:
        # 1. Zone de configuration (Sidebar ou Colonnes)
        with st.expander("⚙️ Paramètres de la simulation", expanded=not st.session_state.sim_lancee):
            col_a, col_b = st.columns(2)
            with col_a:
                st.subheader("Contraintes RH")
                amplitude = st.number_input("Amplitude poste (min)", value=450, help="7h30 = 450 min")
                pause = st.number_input("Pause déjeuner (min)", value=30)
                releve = st.number_input("Temps relève véhicule (min)", value=15)
            with col_b:
                st.subheader("Contraintes Transport")
                max_t = st.slider("Durée max tournée (min)", 30, 240, 120)
                t_coll = st.slider("Temps fixe / arrêt (min)", 2, 20, 10)
        
        # Bouton de lancement
        if st.button("Lancer l'optimisation", use_container_width=True):
            # Préparation des données pour le moteur
            df_dist = st.session_state["data"]["matrice_distance"]
            df_dur = st.session_state["data"]["matrice_duree"]
            df_sites = st.session_state["data"]["accessibilite_sites"] # Ajuster selon votre Import.py
            
            # Ici, on simule une configuration de sites pour l'exemple
            # Idéalement, créer un dictionnaire à partir de st.session_state["data"]["m_flux"]
            sites_config = {} 
            unique_sites = st.session_state["data"]["m_flux"]["Site hospitalier"].unique()
            for s in unique_sites[:10]: # On limite pour le test
                sites_config[s] = {'open': 480, 'close': 1140, 'freq': 3}

            config_rh = {'amplitude': amplitude, 'pause': pause, 'releve': releve}
            
            # Calcul
            resultats = run_optimization(df_dur, sites_config, t_coll, max_t, config_rh)
            st.session_state.resultat_flotte = resultats
            st.session_state.sim_lancee = True
            st.success("Simulation terminée !")

        # 2. AFFICHAGE DES RÉSULTATS (Les onglets demandés)
        if st.session_state.sim_lancee:
            flotte = st.session_state.resultat_flotte
            config_rh = {'amplitude': amplitude, 'pause': pause, 'releve': releve}
            df_dist = st.session_state["data"]["matrice_distance"]
            # Récupération des sites pour la carte (latitude/longitude)
            # Attention : Assurez-vous que l'onglet 'param Sites' contient bien lat/lon
            df_geo = st.session_state["data"].get("param_sites", pd.DataFrame())

            tab_synth, tab_det = st.tabs(["📊 Synthèse", "🚐 Détail des tournées"])

            with tab_synth:
                kpis = calculate_kpis(flotte, config_rh, df_dist)
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Véhicules totaux", kpis["v_total"])
                c2.metric("Postes Chauffeurs", kpis["c_total"])
                c3.metric("Taux d'occupation moy.", f"{kpis['tx_occ']:.1f}%")
                
                c4, c5, c6 = st.columns(3)
                c4.metric("Total Tournées", kpis["t_total"])
                c5.metric("Km totaux", f"{int(kpis['km_total'])} km")
                c6.metric("Km / Chauffeur", f"{int(kpis['km_moy'])} km")

                st.divider()
                st.subheader("Graphe d'occupation de la flotte")
                render_fleet_gantt(flotte)
                
                st.subheader("Nuage des passages par site")
                render_site_passages(flotte)

            with tab_det:
                v_sel = st.selectbox("Choisir un véhicule", list(flotte.keys()))
                
                v_postes = flotte[v_sel]
                render_fleet_gantt(flotte, v_highlight=v_sel)
                
                st.write("---")
                
                # Menu pour les tournées du véhicule
                t_options = []
                for p_idx, p in enumerate(v_postes):
                    for t_idx, t in enumerate(p):
                        t_options.append({"label": f"Chauffeur {p_idx+1} - Tournée {t_idx+1}", "data": t})
                
                sel_t = st.selectbox("Choisir une tournée", t_options, format_func=lambda x: x["label"])
                
                col_map, col_tab = st.columns([2, 1])
                with col_map:
                    render_tournee_map(sel_t["data"], df_geo)
                with col_tab:
                    st.write("**Horaires de passage**")
                    df_res = pd.DataFrame(sel_t["data"])
                    df_res['heure'] = df_res['heure'].apply(lambda x: f"{int(x//60):02d}h{int(x%60):02d}")
                    st.table(df_res)
