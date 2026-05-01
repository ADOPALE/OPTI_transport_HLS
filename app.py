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
from modules.flux_engine import run_flux_optimization, verifier_faisabilite, precalculer_capacites

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
                # Stocker le solveur utilisé pour l'afficher après rerun
                from modules.biologie_engine import ORTOOLS_AVAILABLE
                st.session_state["solveur_utilise"] = "ortools" if ORTOOLS_AVAILABLE else "greedy"
                st.rerun()
            except Exception as e:
                st.error(f"Erreur : {e}")

    if st.session_state.get("sim_lancee"):
        st.success(f"✅ Simulation réussie ! {len(st.session_state.resultat_flotte)} véhicules identifiés.")
        if st.session_state.get("solveur_utilise") == "ortools":
            st.info("🧠 Solution calculée par **OR-Tools** (solveur optimal)")
        elif st.session_state.get("solveur_utilise") == "greedy":
            st.warning("⚠️ Solution calculée par **l'heuristique gloutonne** (OR-Tools indisponible ou sans solution)")
        # Affichage du rapport détaillé stocké par biologie_engine
        rapport = st.session_state.get("bio_rapport")
        if rapport:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("🚐 Véhicules", rapport["nb_vehicules"])
            col2.metric("👤 Postes chauffeurs", rapport["nb_postes"])
            col3.metric("⏱️ Taux occupation moyen", f"{rapport['taux_occupation']:.1%}")
            col4.metric("⏳ Palier optimal", f"{rapport['palier']} min")
            if rapport.get("repassage"):
                st.caption("⚠️ Repassage autorisé sur certains sites (fenêtres incompatibles)")

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
    st.title("🚚 Synthèse Transport — Optimisation OR-Tools")

    # Vérification des prérequis
    donnees_ok = (
        "data" in st.session_state
        and "m_flux"           in st.session_state["data"]
        and "param_vehicules"  in st.session_state["data"]
        and "param_contenants" in st.session_state["data"]
        and "param_sites"      in st.session_state["data"]
        and "matrice_duree"    in st.session_state["data"]
    )
    params_ok = "params_logistique" in st.session_state

    if not donnees_ok:
        st.warning("⚠️ Importez vos données Excel avant de lancer la simulation.")
    elif not params_ok:
        st.warning("⚠️ Validez vos paramètres logistiques dans l'onglet 'Véhicules et paramètres'.")
    else:
        # Sélection du jour
        jour_choisi = st.selectbox(
            "Jour à simuler",
            ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"],
            key="flux_jour_choisi"
        )

        # Budget temps solveur
        time_limit = st.slider(
            "Budget temps OR-Tools par type de véhicule (secondes)",
            min_value=15, max_value=180, value=60, step=15,
            help="Plus la valeur est élevée, plus la solution sera proche de l'optimal."
        )

        # ── Contrôle de faisabilité ────────────────────────────────────────
        if st.button("🔍 Vérifier la faisabilité", use_container_width=True):
            with st.spinner("Vérification en cours..."):
                try:
                    cap = precalculer_capacites(
                        st.session_state["data"]["param_vehicules"],
                        st.session_state["data"]["param_contenants"],
                    )
                    controle = verifier_faisabilite(
                        df_flux           = st.session_state["data"]["m_flux"],
                        df_vehicules      = st.session_state["data"]["param_vehicules"],
                        df_contenants     = st.session_state["data"]["param_contenants"],
                        df_sites          = st.session_state["data"]["param_sites"],
                        capacites         = cap,
                        params_logistique = st.session_state["params_logistique"],
                        jour              = jour_choisi,
                    )
                    st.session_state["flux_controle"] = controle
                except Exception as e:
                    st.error(f"Erreur lors du contrôle : {e}")
                    st.exception(e)

        # Affichage du rapport de faisabilité
        if "flux_controle" in st.session_state:
            ctrl = st.session_state["flux_controle"]
            if ctrl["faisable"]:
                st.success(ctrl["resume"])
            else:
                st.warning(ctrl["resume"])
                with st.expander(f"📋 Voir les {ctrl['nb_flux_ko']} flux non faisables", expanded=False):
                    if not ctrl["details_df"].empty:
                        # Grouper par raison pour un affichage clair
                        for raison, grp in ctrl["details_df"].groupby("Problème"):
                            labels = {
                                "SITE_INCONNU"             : "🔴 Sites inconnus dans param_sites",
                                "AUCUN_VEHICULE_ACCESSIBLE": "🟠 Aucun véhicule accessible sur la liaison",
                                "AUCUNE_CAPACITE"          : "🟡 Véhicule accessible mais capacité incompatible",
                            }
                            st.subheader(labels.get(raison, raison))
                            st.dataframe(
                                grp.drop(columns=["Problème"]),
                                use_container_width=True,
                                hide_index=True
                            )

        st.divider()

        btn_label = (
            "🔄 Relancer la simulation"
            if st.session_state.get("flux_sim_lancee")
            else "🚀 Lancer la simulation"
        )

        if st.button(btn_label, type="primary", use_container_width=True):
            with st.spinner("🧠 Optimisation OR-Tools en cours..."):
                try:
                    resultats = run_flux_optimization(
                        df_flux           = st.session_state["data"]["m_flux"],
                        df_vehicules      = st.session_state["data"]["param_vehicules"],
                        df_contenants     = st.session_state["data"]["param_contenants"],
                        df_sites          = st.session_state["data"]["param_sites"],
                        matrice_duree     = st.session_state["data"]["matrice_duree"],
                        params_logistique = st.session_state["params_logistique"],
                        jour              = jour_choisi,
                        time_limit_seconds= time_limit,
                    )
                    st.session_state["flux_resultats"]  = resultats
                    st.session_state["flux_sim_lancee"] = True
                    st.rerun()
                except Exception as e:
                    st.error(f"Erreur lors de l'optimisation : {e}")
                    st.exception(e)

        # ── AFFICHAGE DES RÉSULTATS (après rerun) ──────────────────────────
        if st.session_state.get("flux_sim_lancee") and "flux_resultats" in st.session_state:
            res     = st.session_state["flux_resultats"]
            rapport = res.get("rapport", {})
            postes  = res.get("postes", [])
            jour_res = res.get("jour", jour_choisi)

            st.divider()

            # ── Métriques globales ──────────────────────────────────────────
            st.subheader(f"📊 Résultats — {jour_res}")

            nb_v_par_type = rapport.get("nb_vehicules_par_type", {})
            nb_p_par_type = rapport.get("nb_postes_par_type", {})
            all_types     = sorted(set(list(nb_v_par_type.keys()) + list(nb_p_par_type.keys())))

            if all_types:
                cols = st.columns(len(all_types) + 2)
                for i, v_type in enumerate(all_types):
                    cols[i].metric(
                        f"🚛 {v_type}",
                        f"{nb_v_par_type.get(v_type, 0)} véh.",
                        f"{nb_p_par_type.get(v_type, 0)} poste(s)"
                    )
                cols[-2].metric("👤 Postes total",   rapport.get("nb_postes_total", 0))
                cols[-1].metric("⏱️ Taux moyen",     f"{rapport.get('taux_moyen', 0):.1%}")

            # Alerte jobs non planifiés
            nb_np = rapport.get("nb_jobs_non_planifies", 0)
            if nb_np > 0:
                st.warning(f"⚠️ {nb_np} job(s) n'ont pas pu être planifiés.")
                with st.expander(f"🔍 Voir les {nb_np} jobs non planifiés", expanded=True):
                    jobs_np = rapport.get("jobs_non_planifies_detail", [])
                    if jobs_np:
                        rows_np = []
                        for item in jobs_np:
                            # Compatibilité : item peut être un dict ou un JobElementaire
                            if isinstance(item, dict):
                                j      = item["job"]
                                raison = item.get("raison", "Non planifié")
                                v_type = item.get("v_type", j.v_type_requis)
                            else:
                                j      = item
                                raison = "Non planifié par OR-Tools"
                                v_type = j.v_type_requis
                            rows_np.append({
                                "Job ID"         : j.job_id,
                                "Flux ID"        : j.flux_id,
                                "Origine"        : j.origine,
                                "Destination"    : j.destination,
                                "Contenant"      : j.type_contenant,
                                "Qté"            : j.nb_contenants,
                                "Propre/Sale"    : j.propre_sale,
                                "Véhicule requis": v_type,
                                "Fenêtre"        : (
                                    f"{int(j.h_dispo//60):02d}h{int(j.h_dispo%60):02d}"
                                    f" → {int(j.h_deadline//60):02d}h{int(j.h_deadline%60):02d}"
                                ),
                                "Raison"         : raison,
                            })
                        st.dataframe(
                            pd.DataFrame(rows_np),
                            use_container_width=True,
                            hide_index=True
                        )
            else:
                st.success("✅ Tous les flux ont été planifiés.")

            # ── Détail des postes chauffeurs ────────────────────────────────
            if postes:
                st.divider()
                st.subheader("📅 Planning des postes chauffeurs")

                # Filtrage par type de véhicule
                types_dispo = sorted({p.v_type for p in postes})
                type_filtre = st.selectbox("Filtrer par type de véhicule", ["Tous"] + types_dispo,
                                           key="flux_type_filtre")
                postes_affiches = postes if type_filtre == "Tous" else [p for p in postes if p.v_type == type_filtre]

                # Tableau récapitulatif des postes
                rows = []
                for p in postes_affiches:
                    h_deb = f"{int(p.h_debut // 60):02d}h{int(p.h_debut % 60):02d}"
                    h_fin = f"{int(p.h_fin   // 60):02d}h{int(p.h_fin   % 60):02d}"
                    rows.append({
                        "Poste"           : p.poste_id,
                        "Type véhicule"   : p.v_type,
                        "Début"           : h_deb,
                        "Fin"             : h_fin,
                        "Amplitude (min)" : round(p.amplitude, 0),
                        "Taux occupation" : f"{p.taux_occupation:.1%}",
                        "Nb missions"     : len(p.missions),
                    })
                if rows:
                    st.dataframe(
                        pd.DataFrame(rows),
                        use_container_width=True,
                        hide_index=True
                    )

                # ── Timeline Gantt de tous les postes ──────────────────
                st.divider()
                st.subheader("⏱️ Timeline des postes")

                import plotly.graph_objects as go
                from datetime import date, datetime, timedelta

                # Couleurs par type d'étape
                COULEURS = {
                    "Prise de poste" : "#4A90D9",
                    "Trajet vide"    : "#B0BEC5",
                    "Chargement"     : "#66BB6A",
                    "Trajet plein"   : "#FFA726",
                    "Livraison"      : "#EF5350",
                    "Attente"        : "#CE93D8",
                    "Pause"          : "#FF7043",
                    "Fin de poste"   : "#78909C",
                }

                def min_to_dt(minutes):
                    """Convertit minutes depuis minuit en datetime (date fictive)."""
                    return datetime(2000, 1, 1) + timedelta(minutes=float(minutes))

                rh_params   = st.session_state["params_logistique"].get("rh", {})
                t_prise      = float(rh_params.get("temps_fixes_prise", 20))
                t_fin        = float(rh_params.get("temps_fixes_fin",   15))
                t_pause      = float(rh_params.get("pause", 30))
                t_pause_seuil = 180.0  # pause obligatoire si amplitude > 3h

                gantt_rows = []

                for p in postes_affiches:
                    label = f"{p.poste_id}"
                    missions_sorted = sorted(p.missions, key=lambda m: m['heure'])

                    # 1. Prise de poste
                    t_debut_reel = p.h_debut
                    gantt_rows.append(dict(
                        Poste=label, Étape="Prise de poste",
                        Début=min_to_dt(t_debut_reel),
                        Fin=min_to_dt(t_debut_reel + t_prise),
                        Info="Préparation / Check véhicule"
                    ))

                    curseur = t_debut_reel + t_prise
                    charge_actuelle = 0  # 0 = vide, >0 = chargé

                    for i, m in enumerate(missions_sorted):
                        h_mission = m['heure']

                        # Attente si le chauffeur arrive avant la mission
                        if h_mission > curseur + 1:
                            gantt_rows.append(dict(
                                Poste=label, Étape="Attente",
                                Début=min_to_dt(curseur),
                                Fin=min_to_dt(h_mission),
                                Info=f"Attente avant {m['site']}"
                            ))

                        # Trajet (vide ou plein selon contexte)
                        # On considère que le trajet précède chaque mission
                        # (la durée de trajet est incluse dans l'écart entre missions)
                        if m['is_pickup']:
                            # Chargement sur site
                            duree_chargement = 10  # approximation
                            gantt_rows.append(dict(
                                Poste=label, Étape="Chargement",
                                Début=min_to_dt(h_mission),
                                Fin=min_to_dt(h_mission + duree_chargement),
                                Info=f"{m['nb_contenants']} {m['type_contenant']} — {m['site']}"
                            ))
                            charge_actuelle += m['nb_contenants']
                            curseur = h_mission + duree_chargement
                        else:
                            # Livraison
                            duree_livraison = 10
                            gantt_rows.append(dict(
                                Poste=label, Étape="Livraison",
                                Début=min_to_dt(h_mission),
                                Fin=min_to_dt(h_mission + duree_livraison),
                                Info=f"{m['nb_contenants']} {m['type_contenant']} → {m['site']}"
                            ))
                            charge_actuelle = max(0, charge_actuelle - m['nb_contenants'])
                            curseur = h_mission + duree_livraison

                        # Trajet vers prochaine mission
                        if i < len(missions_sorted) - 1:
                            h_next = missions_sorted[i + 1]['heure']
                            if h_next > curseur + 1:
                                type_trajet = "Trajet plein" if charge_actuelle > 0 else "Trajet vide"
                                gantt_rows.append(dict(
                                    Poste=label, Étape=type_trajet,
                                    Début=min_to_dt(curseur),
                                    Fin=min_to_dt(h_next),
                                    Info=f"{'Chargé' if charge_actuelle > 0 else 'Vide'}"
                                ))

                    # Pause si amplitude > seuil
                    amplitude = p.h_fin - t_debut_reel
                    if amplitude > t_pause_seuil:
                        milieu = t_debut_reel + amplitude / 2
                        gantt_rows.append(dict(
                            Poste=label, Étape="Pause",
                            Début=min_to_dt(milieu),
                            Fin=min_to_dt(milieu + t_pause),
                            Info="Pause obligatoire"
                        ))

                    # Fin de poste
                    gantt_rows.append(dict(
                        Poste=label, Étape="Fin de poste",
                        Début=min_to_dt(p.h_fin - t_fin),
                        Fin=min_to_dt(p.h_fin),
                        Info="Nettoyage / Clôture"
                    ))

                if gantt_rows:
                    df_gantt = pd.DataFrame(gantt_rows)
                    fig = go.Figure()

                    for etape, couleur in COULEURS.items():
                        df_e = df_gantt[df_gantt["Étape"] == etape]
                        for _, row in df_e.iterrows():
                            fig.add_trace(go.Bar(
                                name=etape,
                                y=[row["Poste"]],
                                x=[(row["Fin"] - row["Début"]).total_seconds() / 60],
                                base=[(row["Début"] - datetime(2000,1,1)).total_seconds() / 60],
                                orientation='h',
                                marker_color=couleur,
                                text=row["Info"],
                                textposition="inside",
                                insidetextanchor="middle",
                                hovertemplate=(
                                    f"<b>{etape}</b><br>"
                                    f"{row['Début'].strftime('%H:%M')} → {row['Fin'].strftime('%H:%M')}<br>"
                                    f"{row['Info']}<extra></extra>"
                                ),
                                showlegend=(row["Poste"] == df_gantt[df_gantt["Étape"] == etape]["Poste"].iloc[0]),
                                legendgroup=etape,
                            ))

                    # Axe X en heures
                    h_min = int(min(p.h_debut for p in postes_affiches) // 60) * 60
                    h_max = int(max(p.h_fin   for p in postes_affiches) // 60 + 1) * 60
                    tickvals = list(range(h_min, h_max + 1, 60))
                    ticktext = [f"{v//60:02d}h00" for v in tickvals]

                    fig.update_layout(
                        barmode='stack',
                        height=max(300, len(postes_affiches) * 60 + 100),
                        xaxis=dict(
                            tickvals=tickvals, ticktext=ticktext,
                            title="Heure", range=[h_min, h_max]
                        ),
                        yaxis=dict(title="Poste", autorange="reversed"),
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        margin=dict(l=10, r=10, t=40, b=40),
                        plot_bgcolor="#1a1a2e",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font_color="white",
                    )
                    st.plotly_chart(fig, use_container_width=True)

                # ── Détail tabulaire d'un poste sélectionné ────────────────
                st.divider()
                st.subheader("🔍 Détail d'un poste")
                poste_ids = [p.poste_id for p in postes_affiches]
                if poste_ids:
                    poste_sel_id = st.selectbox("Choisir un poste", poste_ids, key="flux_poste_sel")
                    poste_sel = next((p for p in postes_affiches if p.poste_id == poste_sel_id), None)
                    if poste_sel and poste_sel.missions:
                        rows_m = []
                        for m in poste_sel.missions:
                            heure = f"{int(m['heure'] // 60):02d}h{int(m['heure'] % 60):02d}"
                            action = "📦 Chargement" if m['is_pickup'] else "🏁 Livraison"
                            rows_m.append({
                                "Heure"       : heure,
                                "Action"      : action,
                                "Site"        : m['site'],
                                "Contenant"   : m['type_contenant'],
                                "Qté"         : m['nb_contenants'],
                                "Propre/Sale" : m['propre_sale'],
                            })
                        st.dataframe(
                            pd.DataFrame(rows_m),
                            use_container_width=True,
                            hide_index=True
                        )

            # ── Graphique de charge par type de véhicule ────────────────────
            jobs_par_type = res.get("jobs_par_type", {})
            if jobs_par_type:
                st.divider()
                st.subheader("📈 Charge horaire théorique par type de véhicule")
                import numpy as np
                heures = list(range(24))
                chart_data = {}
                for v_type, jlist in jobs_par_type.items():
                    vecteur = np.zeros(24)
                    for j in jlist:
                        h_start = max(0, min(23, int(j.h_dispo   / 60)))
                        h_end   = max(0, min(23, int(j.h_deadline / 60)))
                        if h_end > h_start:
                            vecteur[h_start:h_end] += j.nb_contenants / max(h_end - h_start, 1)
                    if vecteur.sum() > 0:
                        chart_data[v_type] = vecteur
                if chart_data:
                    labels = [f"{h:02d}h" for h in heures]
                    st.bar_chart(pd.DataFrame(chart_data, index=labels))
