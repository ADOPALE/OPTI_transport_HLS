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

elif selected == "Simul tournées": # Transport
    st.title("🚀 Simulation Transport Lourd (Flotte Homogène)")
    
    if 'data' in st.session_state:
        # Bouton pour lancer la simulation exhaustive (100 itérations de la semaine)
        if st.button("Lancer l'optimisation Hebdo Homogène", type="primary"):
            with st.spinner("🧠 Analyse de 100 scénarios de semaines complètes pour optimiser la flotte..."):
                try:
                    # Appel du nouveau moteur (simul_flux_2)
                    # La fonction retourne le meilleur score (pic de flotte minimal + km optimisés)
                    #resultats_hebdo = lancer_simulation(st.session_state['data'])
                    
                    # On stocke les résultats dans le session_state
                    st.session_state['planning_detaille'] = resultats_hebdo
                    st.success(f"✅ Analyse terminée. Flotte fixe requise : {resultats_hebdo['kpis']['nb_chauffeurs_max_jour']} camions.")
                except Exception as e:
                    st.error(f"Erreur lors de la simulation : {e}")

        # --- AFFICHAGE DES RÉSULTATS SI DISPONIBLES ---
        if 'planning_detaille' in st.session_state:
            res = st.session_state['planning_detaille']
            k = res["kpis"]
            
            # 1. Affichage des KPIs Globaux (Hebdo)
            st.divider()
            st.subheader("📊 Indicateurs Clés de la Semaine (Meilleur scénario)")
            
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Flotte Fixe (Pic)", f"{k['nb_chauffeurs_max_jour']} Véhicules")
            col2.metric("Distance Totale", f"{k['distance_totale']:,.0f} km")
            col3.metric("Remplissage Moyen", f"{k['remplissage_moyen']:.1f}%")
            col4.metric("Total Tournées", k["nb_tournees"])
            
            # 2. Aperçu rapide du Lundi (Jour de référence souvent chargé)
            st.subheader("🔍 Aperçu : Planning du Lundi")
            
            # On récupère les données du lundi dans le détail des jours
            lundi_data = res["detail_jours"].get("Lundi", {})
            
            if lundi_data:
                col_a, col_b = st.columns(2)
                col_a.info(f"Nombre de chauffeurs mobilisés : {len(lundi_data['chauffeurs'])}")
                col_b.info(f"Nombre de tournées : {len(lundi_data['tournees'])}")
                
                # Petit tableau récapitulatif des tournées du lundi
                liste_t = []
                for t in lundi_data["tournees"]:
                    liste_t.append({
                        "ID": t.id,
                        "Départ HSJ": f"{int(t.h_debut_hsj//60):02d}h{int(t.h_debut_hsj%60):02d}",
                        "Fin HSJ": f"{int(t.h_fin_hsj//60):02d}h{int(t.h_fin_hsj%60):02d}",
                        "KM": f"{t.km_totaux:.1f}",
                        "Type": "Sale" if t.is_sale_tournee else "Propre",
                        "Remplissage": f"{(t.remplissage_L / 7.5)*100:.1f}%" # 7.5m par défaut pour PL 19T
                    })
                
                st.table(pd.DataFrame(liste_t).head(10)) # Affiche les 10 premières tournées
                st.caption("Allez dans l'onglet 'Synthèse transport' ou 'Détail tournées' pour voir le reste de la semaine.")
            else:
                st.warning("Aucune donnée disponible pour le Lundi dans ce scénario.")
                
    else: 
        st.error("⚠️ Importez des données d'abord dans l'onglet 'Importer Données'.")
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
