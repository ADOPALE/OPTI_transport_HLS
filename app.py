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
    preparer_flux_complets_du_jour,
    tunnel_consolidation_flux
)
from modules.sequencage_engine import trouver_meilleure_configuration_journee, afficher_controle_coherence
import modules.Resultats_simul_flux as res_flux

# --- NOUVEAU MOTEUR DE CHAÎNAGE & POSTES (refonte étapes 3-4) ---
from modules.moteur_postes import optimiser_postes_jour
import modules.resultats_postes as rp

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

# =====================================================================
# SYNTHÈSE TRANSPORT — refonte : moteur de chaînage + postes
# =====================================================================
elif selected == "Synthèse transport":
    st.title("🚚 Synthèse Transport — Dimensionnement flotte & postes")
    st.markdown("---")

    # ── Prérequis ─────────────────────────────────────────────────────
    if 'data' not in st.session_state:
        st.error("⚠️ Importez les données d'abord (onglet 'Importer Données').")
        st.stop()
    if 'df_sequence_type' not in st.session_state:
        st.warning("⚠️ Générez d'abord la 'Séquence Type' (onglet 'Simul tournées').")
        st.stop()
    if 'params_logistique' not in st.session_state:
        st.warning("⚠️ Validez les paramètres logistiques (onglet 'Véhicules et paramètres').")
        st.stop()

    # ── Données ───────────────────────────────────────────────────────
    df_recurrent  = st.session_state['df_sequence_type']
    df_specifique = st.session_state.get('df_flux_specifique', pd.DataFrame())
    df_vehicules  = st.session_state['data']['param_vehicules']
    df_contenants = st.session_state['data']['param_contenants']
    df_sites      = st.session_state['data']['param_sites']
    matrice_duree = st.session_state['data']['matrice_duree']   # DataFrame brut
    params_log    = st.session_state.get('params_logistique', {})

    # ── Paramétrage de la simulation ─────────────────────────────────
    st.subheader("1️⃣ Paramétrage de la simulation")
    tous_jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    col_j, col_o, col_b = st.columns([3, 2, 1])
    with col_j:
        jours_selectionnes = st.multiselect(
            "Jours à simuler", options=tous_jours, default=["Lundi"],
            help="Sélectionnez un ou plusieurs jours."
        )
    with col_o:
        autoriser_tournees = st.toggle(
            "Tournées multi-arrêts",
            value=True,
            help="Regroupe les reliquats en tournées de distribution (1 origine → "
                 "plusieurs livraisons) ou de ramassage (plusieurs collectes → 1 destination)."
        )
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        lancer = st.button("🚀 Lancer", type="primary", use_container_width=True)

    # ── Lancement ─────────────────────────────────────────────────────
    if lancer:
        if not jours_selectionnes:
            st.warning("⚠️ Sélectionnez au moins un jour.")
            st.stop()
        try:
            resultats = {}
            with st.status(f"Optimisation ({len(jours_selectionnes)} jour(s))...", expanded=True) as status:
                for jour in jours_selectionnes:
                    st.write(f"🔄 **{jour}** — préparation des flux...")
                    df_jour = preparer_flux_complets_du_jour(df_recurrent, df_specifique, jour)

                    st.write(f"  ↳ 🧠 Chaînage & construction des postes...")
                    res = optimiser_postes_jour(
                        df_jour, df_vehicules, df_contenants, df_sites,
                        matrice_duree, params_log, nom_jour=str(jour),
                        autoriser_tournees=autoriser_tournees,
                    )
                    resultats[jour] = res
                    m = res["metriques"]
                    st.write(
                        f"  ✅ **{jour}** : {m['nb_postes']} poste(s), "
                        f"{m['nb_vehicules_total']} véhicule(s), "
                        f"chargé/roulage {m['taux_charge_global']}%"
                    )
                status.update(label="✅ Optimisation terminée !", state="complete")

            st.session_state['postes_resultats'] = resultats
            st.session_state['postes_jours'] = jours_selectionnes

        except Exception as e:
            st.error(f"Erreur lors de l'optimisation : {e}")
            st.exception(e)

    # ── Résultats ─────────────────────────────────────────────────────
    if 'postes_resultats' not in st.session_state:
        st.stop()

    resultats = st.session_state['postes_resultats']

    st.divider()
    st.subheader("2️⃣ Récapitulatif hebdomadaire")
    rp.afficher_recap_jours(resultats)

    st.divider()
    st.subheader("3️⃣ Détail opérationnel par jour")

    jours_dispo = st.session_state.get('postes_jours', list(resultats.keys()))
    if not jours_dispo:
        st.stop()

    jour_sel = st.selectbox("Choisir un jour à détailler", jours_dispo)
    res = resultats[jour_sel]
    postes = res["postes"]

    if not postes:
        st.info("Aucun poste pour ce jour.")
        st.stop()

    # Avertissement fenêtres tendues (incohérences de données)
    nb_tendues = res.get("metriques", {}).get("nb_missions_non_traitees", 0)
    if nb_tendues:
        st.warning(f"⚠️ {nb_tendues} flux ont une fenêtre horaire incohérente ou trop courte "
                   f"dans le fichier source (livraison avant mise à dispo, ou durée > fenêtre). "
                   f"Ils sont planifiés malgré tout, mais à vérifier dans l'Excel.")

    # Courbe de concurrence = preuve du lissage (pas de pic matinal)
    st.markdown("#### 📉 Lissage de la charge")
    rp.afficher_courbe_concurrence(res)

    # Filtre par type de véhicule
    types_dispo = sorted({p.v_type for p in postes})
    type_filtre = st.selectbox("Filtrer par type de véhicule", ["Tous"] + types_dispo, key="filtre_type_postes")
    postes_affiches = postes if type_filtre == "Tous" else [p for p in postes if p.v_type == type_filtre]

    # Gantt groupé par véhicule (montre la relève)
    st.markdown(f"#### 📅 Planning par véhicule — {jour_sel}")
    rp.afficher_gantt_postes(postes_affiches, titre=f"Planning {jour_sel}")

    # Onglets détail
    st.divider()
    tab_postes, tab_sites = st.tabs(["🔍 Détail par poste", "🏥 Détail par site"])
    with tab_postes:
        rp.afficher_detail_postes(postes_affiches)
    with tab_sites:
        rp.afficher_detail_sites(postes_affiches)
