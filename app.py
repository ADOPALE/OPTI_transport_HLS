import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
from streamlit_folium import st_folium
import folium
import pandas as pd
import plotly.express as px
import math

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
from modules.Prep_simul_flux import segmenter_flux, choix_Jmax, simuler_lissage_flotte, afficher_graphique_charge_empilee
from modules.sim_engine import (
    traitement_flux_recurrents, 
    ordonnancer_flotte_optimale,
    preparer_flux_complets_du_jour, # Ajoutez celle-ci
    tunnel_consolidation_flux# Ajoutez celle-ci
)
from modules.sequencage_engine import trouver_meilleure_configuration_journee, afficher_controle_coherence
import modules.Resultats_simul_flux as res_flux

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

elif selected == "Simul tournées":
    st.title("🚀 Simulation Transport Lourd")
    
    if 'data' in st.session_state:
        df_flux_brut = st.session_state['data']['m_flux']
        
        # --- MODIFICATION ICI : SAUVEGARDE SYSTÉMATIQUE ---
        with st.expander("📊 Détails de la segmentation des flux", expanded=False):
            df_recurrent, df_specifique = segmenter_flux(df_flux_brut)
            
            # On les enregistre dans le session_state pour qu'ils survivent aux boutons
            st.session_state['df_recurrent'] = df_recurrent
            st.session_state['df_flux_specifique'] = df_specifique
            
            col1, col2 = st.columns(2)
            col1.metric("Flux Récurrents (L-V)", len(df_recurrent))
            col2.metric("Flux Spécifiques", len(df_specifique))
        
        st.divider()

        # 3. Calcul de la Séquence Type (Etape 2.a.ii)
        st.subheader("📌 Génération de la Séquence Type (Jmax)")
        
        if st.button("Lancer le calcul du Jmax", type="primary", use_container_width=True):
            with st.spinner("🧠 Analyse des poids fictifs..."):
                try:
                    # Utiliser les DataFrames du session_state
                    df_sequence_type = choix_Jmax(
                        df_recurrent=st.session_state['df_recurrent'], # Version persistante
                        df_vehicules=st.session_state['data']['param_vehicules'],
                        df_contenants=st.session_state['data']['param_contenants'],
                        matrice_duree=st.session_state['data']['matrice_duree'],
                        df_sites=st.session_state['data']['param_sites']
                    )
                    
                    st.session_state['df_sequence_type'] = df_sequence_type
                    st.success("✅ Séquence type générée !")
                except Exception as e:
                    st.error(f"Erreur lors du calcul : {e}")
                
    else: 
        st.error("⚠️ Veuillez importer les données dans l'onglet 'Importer Données' avant de continuer.")
        
elif selected == "Synthèse transport":
    if 'df_sequence_type' in st.session_state:
        st.title("🚚 Synthèse Hebdomadaire & Détail Opérationnel")
        
        # 1. RÉCUPÉRATION DES DONNÉES
        df_recurrent = st.session_state.get('df_sequence_type')
        df_specifique = st.session_state.get('df_flux_specifique', pd.DataFrame())
        df_vehicules = st.session_state['data']['param_vehicules']
        df_contenants = st.session_state['data']['param_contenants']
        df_sites = st.session_state['data']['param_sites']
        # On récupère, on définit la 1ère colonne comme index, et on convertit en dictionnaire
        matrice_duree = st.session_state['data']['matrice_duree'].set_index(st.session_state['data']['matrice_duree'].columns[0]).to_dict('index')
        #matrice_duree = st.session_state['data'].get('matrice_duree')
        params_logistique = st.session_state.get('params_logistique')

        # Fonction utilitaire pour l'affichage
        def fmt_heure_safe(val):
            try:
                if pd.isna(val) or val is None: return "--:--"
                h = int(val // 60)
                m = int(val % 60)
                return f"{h:02d}:{m:02d}"
            except: return "Err"

        # --- BOUTON DE LANCEMENT GLOBAL ---
        if st.button("🚀 Lancer la simulation hebdomadaire (Pipe Complet)", type="primary", use_container_width=True):
            if matrice_duree is None:
                st.error("⚠️ Matrice de temps introuvable.")
            else:
                try:
                    jours_semaine = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
                    resultats_hebdo = []
                    dict_detail_sj = {}
                    dict_postes_par_jour = {} # Pour stocker les plannings
                    
                    from modules.sim_engine import (
                        preparer_flux_complets_du_jour, 
                        tunnel_consolidation_flux, 
                        calculer_nmax_par_type
                    )
                    from modules.sequencage_engine import trouver_meilleure_configuration_journee

                    with st.status("Exécution du Pipe Logistique...", expanded=True) as status:
                        for jour in jours_semaine[:1]:
                            st.write(f"🔄 Traitement du **{jour}**...")
                            
                            # A. Préparation & Consolidation
                            df_complet_jour = preparer_flux_complets_du_jour(df_recurrent, df_specifique, jour)
                            liste_globale_sj = tunnel_consolidation_flux(
                                df_complet_jour, df_vehicules, df_contenants, df_sites, matrice_duree
                            )
                            dict_detail_sj[jour] = liste_globale_sj
                            
                            # B. Calcul Intensité (Besoin théorique)
                            intensite_dict = calculer_nmax_par_type(liste_globale_sj)

                            st.write(f"**📈 Courbe d'intensité théorique du Lundi")
                            labels_h = [f"{int(i*30//60):02d}:{(i*30)%60:02d}" for i in range(48)]
                            st.area_chart(pd.DataFrame(intensite_dict, index=labels_h))
                            
                            # C. Séquençage Optimisé (Recherche du minimum de camions réels)
                            st.write(f"  ↳ 🧠 Optimisation de l'ordonnancement...")
                            res_opti = trouver_meilleure_configuration_journee(
                                liste_globale_sj, intensite_dict, df_vehicules, matrice_duree, params_logistique
                            )
                            
                            if res_opti:
                                dict_postes_par_jour[jour] = res_opti["postes"]
                                n_camions_total = len(res_opti["postes"])
                                
                                # D. Enregistrement pour le récap
                                comptage_jour = {"Jour": jour, "Véhicules Réels": n_camions_total}
                                # On ventile par type de poste pour le tableau
                                for p in res_opti["postes"]:
                                    key = f"{p.vehicule_type}"
                                    comptage_jour[key] = comptage_jour.get(key, 0) + 1
                                
                                resultats_hebdo.append(comptage_jour)

                                afficher_controle_coherence(liste_globale_sj, res_opti["postes"])
                            else:
                                st.error(f"Impossible de trouver une solution pour {jour}")

                        # Sauvegarde globale
                        st.session_state['df_recap_hebdo'] = pd.DataFrame(resultats_hebdo).fillna(0)
                        st.session_state['dict_detail_sj'] = dict_detail_sj
                        st.session_state['dict_postes_par_jour'] = dict_postes_par_jour
                        status.update(label="✅ Simulation hebdomadaire terminée !", state="complete")
                
                except Exception as e:
                    st.error(f"Erreur lors du pipe : {e}")
                    st.exception(e)

        # --- AFFICHAGE DES RÉSULTATS ---
        if 'df_recap_hebdo' in st.session_state:
            st.divider()
            st.subheader("📊 Récapitulatif du Dimensionnement")
            st.dataframe(st.session_state['df_recap_hebdo'], use_container_width=True)

            st.divider()
            st.subheader("🔍 Analyse Opérationnelle par Jour")
            jour_sel = st.selectbox("Choisir un jour pour voir le détail :", ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"])
            
            # --- 1. Graphique de Charge ---
            liste_sj_jour = st.session_state['dict_detail_sj'].get(jour_sel, [])
            if liste_sj_jour:
                from modules.sim_engine import calculer_nmax_par_type
                intensite_dict = calculer_nmax_par_type(liste_sj_jour)
                
                st.write(f"**📈 Courbe d'intensité théorique ({jour_sel})**")
                labels_h = [f"{int(i*30//60):02d}:{(i*30)%60:02d}" for i in range(48)]
                st.area_chart(pd.DataFrame(intensite_dict, index=labels_h))

            # --- 2. Planning Gantt (Séquençage Réel) ---
            postes_jour = st.session_state.get('dict_postes_par_jour', {}).get(jour_sel)
            if postes_jour:
                st.write(f"**📅 Planning Gantt des chauffeurs ({jour_sel})**")
                type_choisi = st.selectbox("Choisir un type", df_vehicules['Types'].unique())
                res_flux.afficher_gantt_chauffeur_detaille(
                    postes_jour, 
                    type_choisi, 
                    liste_globale_sj  # <--- Ajout du 3ème argument indispensable
                )

                
                
            

    else:
        st.warning("⚠️ Veuillez générer la 'Séquence Type' avant de lancer cette synthèse.")
