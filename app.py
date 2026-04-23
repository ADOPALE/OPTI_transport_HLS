import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
from streamlit_folium import st_folium
import folium
import pandas as pd
import plotly.express as px

# --- IMPORTS DES MODULES ---
from modules.GeoMatrix import run_matrix_tool
from modules.Import import show_import
from modules.check_flux import show_flux_control_charts
from modules.param_bio import show_biologie_page
from modules.biologie_engine import run_optimization
from modules.resultats_bio import (
    afficher_stats_vehicules, 
    afficher_stats_chauffeurs, 
    afficher_stats_sites, 
    afficher_detail_flotte_vehicules, 
    afficher_detail_itineraire
)
from modules.param_flux import afficher_parametres_logistique
from modules.simul_flux import segmenter_flux, choix_Jmax
from modules.Resultats_simul_flux import afficher_resultats_complets

# --------- FONCTIONS UI ------------
def show_home():
    st.title("📍 Optimisation des flux logistiques")
    st.markdown("---")
    st.markdown("""
    ### Bienvenue sur l'outil de simulation ADOPALE x CHU de Nantes
    Cet outil vous permet de modéliser, visualiser et optimiser vos tournées de distribution et de biologie.
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

def show_simulation_page():
    st.title("🏎️ Optimisation des tournées Biologie")
    st.markdown("---")
    if "data" not in st.session_state or "matrice_duree" not in st.session_state["data"]:
        st.error("⚠️ Matrice de durée manquante. Importez vos données d'abord.")
        return
    if "biologie_config" not in st.session_state:
        st.warning("⚠️ Configuration manquante. Validez vos paramètres dans l'onglet 'Paramétrage BIO'.")
        return
     # Vérification de la présence de 'param_sites'
    if 'param_sites' not in st.session_state['data']:
        st.error("⚠️ 'param_sites' est manquant dans les données. Veuillez vérifier votre fichier d'import.")
        return  # Sortir de la fonction si la clé est manquante
    else:
        param_sites = st.session_state['data']['param_sites']
        if not isinstance(param_sites, pd.DataFrame):
            st.error("⚠️ Les données de 'param_sites' ne sont pas un DataFrame.")
            return  # Sortir de la fonction si ce n'est pas un DataFrame
        elif 'Libellé' not in param_sites.columns:
            st.error("⚠️ La colonne 'Libellé' est manquante dans 'param_sites'.")
            return  # Sortir de la fonction si la colonne 'Libellé' est manquante
        else:
            st.write(f"'param_sites' est correctement chargé avec {len(param_sites)} lignes.")

    config = st.session_state["biologie_config"]
    btn_label = "🚀 Relancer la simulation" if st.session_state.get("sim_lancee") else "🚀 Lancer la simulation"
    
    if st.button(btn_label, use_container_width=True, type="primary"):
        with st.spinner("🧠 Calcul de l'itinéraire optimal..."):
            try:
                resultats = run_optimization(
                    m_duree_df=st.session_state["data"]["matrice_duree"],
                    sites_config=config["sites"],
                    temps_collecte=config["temps_collecte"],
                    max_tournee=config["duree_max"],
                    souplesse=st.session_state.get("souplesse_fusion", False)
                )
                st.session_state.resultat_flotte = resultats
                st.session_state.sim_lancee = True
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

    if st.session_state.get("sim_lancee"):
        st.success(f"✅ Simulation réussie ! {len(st.session_state.resultat_flotte)} véhicules identifiés.")

# ------------ INITIALISATION APP ---------------
st.set_page_config(layout="wide", page_title="Logistique CHU Nantes & ADOPALE")

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
LOGO_ADOPALE = ASSETS_DIR / "ADOPALE.jpg"
LOGO_CHU = ASSETS_DIR / "CHU Nantes.png"
TEMPLATE_FILE = ASSETS_DIR / "Template_vierge.xlsx"

if "active_menu" not in st.session_state:
    st.session_state.active_menu = "Accueil"

with st.sidebar:
    col1, col2 = st.columns(2)
    with col1:
        if LOGO_ADOPALE.exists(): st.image(str(LOGO_ADOPALE), use_container_width=True)
    with col2:
        if LOGO_CHU.exists(): st.image(str(LOGO_CHU), use_container_width=True)

    st.divider()
    menu_styles = {
        "container": {"background-color": "white", "padding": "0"},
        "icon": {"color": "#00558E", "font-size": "18px"},
        "nav-link": {"color": "black", "font-size": "14px", "font-weight": "bold", "margin": "0px"},
        "nav-link-selected": {"background-color": "#e1e4e8", "color": "black"},
    }

    st.markdown("### 💾 DONNÉES DE BASE")
    sel_data = option_menu(None, ["Accueil", "Outil calcul matrices", "Importer Données"], 
                           icons=["house", "grid", "cloud-upload"], styles=menu_styles, key="m1")

    st.markdown("### 🧪 BIOLOGIE")
    sel_bio = option_menu(None, ["Paramétrage BIO", "Simul tournées BIO", "Synthèse BIO", "Détail tournées BIO"], 
                          icons=["gear", "play", "graph-up", "map"], styles=menu_styles, key="m2")

    st.markdown("### 🚚 DISTRIBUTION")
    sel_dist = option_menu(None, ["Vérif volumes à distribuer", "Véhicules et paramètres", "Simul tournées", "Synthèse transport", "Détail tournées"], 
                           icons=["bar-chart", "truck", "play", "clipboard", "list-task"], styles=menu_styles, key="m3")

    # LOGIQUE DE SYNCHRONISATION
    if sel_data != st.session_state.get('p_data'):
        st.session_state.active_menu = sel_data
        st.session_state.p_data = sel_data
    elif sel_bio != st.session_state.get('p_bio'):
        st.session_state.active_menu = sel_bio
        st.session_state.p_bio = sel_bio
    elif sel_dist != st.session_state.get('p_dist'):
        st.session_state.active_menu = sel_dist
        st.session_state.p_dist = sel_dist

    selected = st.session_state.active_menu

# --- ROUTAGE DES PAGES ---
if selected == "Accueil":
    show_home()
elif selected == "Outil calcul matrices":
    run_matrix_tool()
elif selected == "Importer Données":
    show_import()
elif selected == "Vérif volumes à distribuer":
    st.title("📦 Contrôle des volumes")
    if "data" in st.session_state: show_flux_control_charts()
    else: st.warning("Importez des données d'abord.")

elif selected == "Paramétrage BIO":
    show_biologie_page()
elif selected == "Simul tournées BIO":
    show_simulation_page()
elif selected == "Synthèse BIO":
    st.title("📊 Synthèse Biologie")
    if st.session_state.get("sim_lancee"):
        afficher_stats_vehicules(st.session_state.resultat_flotte, st.session_state["data"]["matrice_distance"])
        afficher_stats_chauffeurs(st.session_state.resultat_flotte, st.session_state["biologie_config"]["rh"])
        afficher_stats_sites(st.session_state.resultat_flotte)
    else: st.info("Lancez la simulation BIO.")

elif selected == "Détail tournées BIO":
    st.title("📋 Détail BIO")
    if st.session_state.get("sim_lancee"):
        res = st.session_state.resultat_flotte
        df_dist = st.session_state["data"]["matrice_distance"]
        df_adresses = st.session_state["data"].get("adresses", st.session_state["data"].get("df_sites"))
        sites_adresses = pd.Series(df_adresses.adresse.values, index=df_adresses.site.str.upper()).to_dict()
        v_sel, vac_sel = afficher_detail_flotte_vehicules(res, df_dist)
        if v_sel: afficher_detail_itineraire(v_sel, vac_sel, sites_adresses, sites_adresses.get("HLS"))
    else: st.info("Lancez la simulation BIO.")

elif selected == "Véhicules et paramètres":
    afficher_parametres_logistique()

elif selected == "Simul tournées":  # Transport
    st.title("🚀 Simulation Transport Lourd")
    
    if 'data' in st.session_state:
        # 1. Extraction du DataFrame brut
        df_flux_brut = st.session_state['data']['m_flux']
        
        # 2. Appel de la segmentation (Etape 2.a.i)
        with st.expander("📊 Détails de la segmentation des flux", expanded=False):
            df_recurrent, df_specifique = segmenter_flux(df_flux_brut)
            col1, col2 = st.columns(2)
            col1.metric("Flux Récurrents (L-V)", len(df_recurrent))
            col2.metric("Flux Spécifiques", len(df_specifique))
        
        st.divider()

        # 3. Calcul de la Séquence Type (Etape 2.a.ii)
        st.subheader("📌 Génération de la Séquence Type (Jmax)")
        
        if st.button("Lancer le calcul du Jmax", type="primary", use_container_width=True):
            with st.spinner("🧠 Analyse des poids fictifs (Bin Packing + Accès Sites)..."):
                try:
                    # On passe bien les DataFrames stockés dans le session_state
                    df_sequence_type = choix_Jmax(
                        df_recurrent=df_recurrent,
                        df_vehicules=st.session_state['data']['param_vehicules'],
                        df_contenants=st.session_state['data']['param_contenants'],
                        matrice_duree=st.session_state['data']['matrice_duree'],
                        df_sites=st.session_state['data']['param_sites']
                    )
                    
                    st.session_state['df_sequence_type'] = df_sequence_type
                    st.success("✅ Séquence type générée ! Vérifiez le jour retenu dans votre console.")
                except Exception as e:
                    st.error(f"Erreur lors du calcul : {e}")
                    # Optionnel pour débugger : st.exception(e)

        # 4. Affichage du résultat
        if 'df_sequence_type' in st.session_state:
            st.write("### 📋 Tableau de la Séquence Type")
            st.dataframe(st.session_state['df_sequence_type'], use_container_width=True)

        # 5. Calcul de la Flotte Théorique (Etape 2.b)
        if 'df_sequence_type' in st.session_state:
            st.divider()
            st.subheader("🚛 Estimation de la Flotte Théorique (Cible)")
            
            # On vérifie si les paramètres logistiques sont bien configurés
            if "params_logistique" not in st.session_state:
                st.warning("⚠️ Pour calculer la flotte, veuillez d'abord configurer vos véhicules et le taux de remplissage dans l'onglet 'Paramètres'.")
            else:
                if st.button("Calculer le besoin en véhicules", type="secondary", use_container_width=True):
                    with st.spinner("⏳ Lissage des flux sur les fenêtres horaires..."):
                        try:
                            # Appel de ta fonction de lissage
                            flotte_theorique = simuler_lissage_flotte(
                                df_sequence_type=st.session_state['df_sequence_type'],
                                df_vehicules=st.session_state['data']['param_vehicules'],
                                df_contenants=st.session_state['data']['param_contenants'],
                                matrice_duree=st.session_state['data']['matrice_duree'],
                                df_sites=st.session_state['data']['param_sites']
                            )
                            
                            if flotte_theorique:
                                # Affichage des résultats sous forme de colonnes
                                st.write("### 📊 Besoin maximum par type de véhicule")
                                cols = st.columns(len(flotte_theorique))
                                
                                for i, (v_type, nb) in enumerate(flotte_theorique.items()):
                                    with cols[i]:
                                        st.metric(label=f"🚚 {v_type}", value=f"{nb} unités")
                                
                                # Stockage pour la suite (optimisation)
                                st.session_state['flotte_theorique'] = flotte_theorique
                                
                                st.info("💡 *Ce nombre représente le pic de véhicules nécessaires en simultané pour respecter vos fenêtres horaires sans optimisation de tournées.*")
                            else:
                                st.warning("Aucun besoin de véhicule détecté. Vérifiez vos quantités et horaires.")
                                
                        except Exception as e:
                            st.error(f"Erreur lors du calcul de la flotte : {e}")
                
    else: 
        st.error("⚠️ Veuillez importer les données dans l'onglet 'Importer Données' avant de continuer.")
        
elif selected == "Synthèse transport":
    if 'planning_detaille' in st.session_state:
        # Utilise la clé 'param_contenants' conforme à ta note technique
        afficher_resultats_complets(
            st.session_state['planning_detaille'], 
            st.session_state['data']['param_vehicules'], 
            st.session_state['data']['param_contenants'] # <--- Vérifie bien l'orthographe ici
        )
    else:
        st.info("⚠️ Aucune simulation n'est en mémoire. Allez dans l'onglet 'Simul tournées' et cliquez sur Lancer.")
