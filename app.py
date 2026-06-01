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
    if 'df_sequence_type' not in st.session_state:
        st.warning("⚠️ Veuillez générer la 'Séquence Type' avant de lancer cette synthèse.")
        st.stop()

    st.title("🚚 Simulation & Planning Transport")

    # ── Données ────────────────────────────────────────────────────────────
    df_recurrent     = st.session_state['df_sequence_type']
    df_specifique    = st.session_state.get('df_flux_specifique', pd.DataFrame())
    df_vehicules     = st.session_state['data']['param_vehicules']
    df_contenants    = st.session_state['data']['param_contenants']
    df_sites         = st.session_state['data']['param_sites']
    matrice_duree    = st.session_state['data']['matrice_duree'].set_index(
                           st.session_state['data']['matrice_duree'].columns[0]).to_dict('index')
    params_logistique = st.session_state.get('params_logistique', {})

    def fmt_h(val):
        try:
            if val is None or (isinstance(val, float) and math.isnan(val)): return "--:--"
            return f"{int(val//60):02d}h{int(val%60):02d}"
        except: return "??"

    from modules.sim_engine import (preparer_flux_complets_du_jour,
                                     tunnel_consolidation_flux, calculer_nmax_par_type)
    from modules.sequencage_engine import trouver_meilleure_configuration_journee

    # ── Sélection des jours ────────────────────────────────────────────────
    st.subheader("1️⃣ Paramétrage de la simulation")
    tous_jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    col_j, col_b = st.columns([3, 1])
    with col_j:
        jours_selectionnes = st.multiselect(
            "Jours à simuler", options=tous_jours, default=["Lundi"],
            help="Sélectionnez un ou plusieurs jours."
        )
    with col_b:
        st.markdown("<br>", unsafe_allow_html=True)
        lancer = st.button("🚀 Lancer la simulation", type="primary", use_container_width=True)

    if lancer:
        if not jours_selectionnes:
            st.warning("⚠️ Sélectionnez au moins un jour.")
            st.stop()
        if matrice_duree is None:
            st.error("⚠️ Matrice de durée introuvable.")
            st.stop()
        try:
            resultats_hebdo   = []
            dict_detail_sj    = {}
            dict_postes_par_jour = {}

            with st.status(f"Simulation en cours ({len(jours_selectionnes)} jour(s))...", expanded=True) as status:
                for jour in jours_selectionnes:
                    st.write(f"🔄 **{jour}** — préparation des flux...")
                    df_jour       = preparer_flux_complets_du_jour(df_recurrent, df_specifique, jour)
                    liste_sj_jour = tunnel_consolidation_flux(
                        df_jour, df_vehicules, df_contenants, df_sites, matrice_duree,
                        nom_jour=str(jour))
                    nb_sj_total = len(liste_sj_jour)
                    intensite   = calculer_nmax_par_type(liste_sj_jour)

                    # ── DEBUG BIOLOGIE ──────────────────────────────────
                    sj_bio = [sj for sj in liste_sj_jour
                              if any('BIOLOGIE' in str(j.contenant).upper()
                                     for j in sj.liste_jobs)]
                    for sj in sj_bio:
                        h_d = round(sj.h_dispo_min / 60, 2)
                        h_dl = round(sj.h_deadline_min / 60, 2)
                        st.write(f"🧪 BIO {sj.super_job_id} | dispo={sj.h_dispo_min:.0f}min ({h_d}h) | deadline={sj.h_deadline_min:.0f}min ({h_dl}h) | v_type={sj.v_type}")
                    # ── FIN DEBUG ───────────────────────────────────────

                    st.write(f"  ↳ 🧠 Séquençage ({nb_sj_total} blocs)...")
                    res = trouver_meilleure_configuration_journee(
                        liste_sj_jour, intensite, df_vehicules, matrice_duree, params_logistique)

                    if res:
                        postes = res["postes"]
                        dict_postes_par_jour[jour] = postes

                        # ── Stocker UNIQUEMENT les SJ réellement exécutés ──
                        # Les SJ non traités sont exclus de dict_detail_sj pour
                        # garantir la cohérence avec les tableaux par poste/site.
                        sj_nt = res.get("sj_non_traites", [])
                        sj_nt_ids = {sj.super_job_id for sj in sj_nt}
                        dict_detail_sj[jour] = [
                            sj for sj in liste_sj_jour
                            if sj.super_job_id not in sj_nt_ids
                        ]

                        if sj_nt:
                            st.session_state.setdefault("sj_non_traites_par_jour", {})[jour] = sj_nt

                        # Compter par type de véhicule
                        types_v = {}
                        km_par_type = {}
                        for p in postes:
                            vt = p.vehicule_type
                            types_v[vt]    = types_v.get(vt, 0) + 1
                            km_par_type[vt] = km_par_type.get(vt, 0)  # km calculés si dispo

                        # Vérifier flux traités
                        # SJ_ID dans l'historique = super_job_id (pas flux_id)
                        sj_ids_traites = set()
                        for p in postes:
                            for ev in p.historique:
                                if ev["Activite"] == "EN_MISSION" and ev["SJ_ID"] != "N/A":
                                    sj_ids_traites.add(ev["SJ_ID"])
                        # Comparer avec les super_job_id de la liste
                        sj_ids_total     = {sj.super_job_id for sj in liste_sj_jour}
                        sj_non_traites_ids = sj_ids_total - sj_ids_traites
                        couverture = f"✅ 100%" if not sj_non_traites_ids else f"⚠️ {len(sj_non_traites_ids)} SJ non traités"

                        row = {"Jour": jour,
                               "Flux couverts": couverture,
                               "Postes total": len(postes),
                               "Chauffeurs": len(postes)}
                        for vt, n in types_v.items():
                            row[f"Véh. {vt}"] = n
                        resultats_hebdo.append(row)
                        st.write(f"  ✅ {jour} : {len(postes)} poste(s) — {couverture}")
                    else:
                        resultats_hebdo.append({"Jour": jour, "Flux couverts": "❌ Échec", "Postes total": 0})
                        st.error(f"❌ Aucune solution pour {jour}")

            st.session_state['recap_hebdo']        = pd.DataFrame(resultats_hebdo).fillna(0)
            st.session_state['dict_detail_sj']     = dict_detail_sj
            st.session_state['dict_postes_par_jour'] = dict_postes_par_jour
            st.session_state['jours_simules']      = jours_selectionnes
            status.update(label="✅ Simulation terminée !", state="complete")

        except Exception as e:
            st.error(f"Erreur lors du pipe : {e}")
            st.exception(e)

    # ── Résultats ──────────────────────────────────────────────────────────
    if 'recap_hebdo' not in st.session_state:
        st.stop()

    st.divider()
    st.subheader("2️⃣ Récapitulatif")
    st.dataframe(st.session_state['recap_hebdo'], use_container_width=True, hide_index=True)

    # ── Détail par jour ───────────────────────────────────────────────────
    # ── Logs de debug Nmax ───────────────────────────────────────────────────
    if st.session_state.get("debug_logs"):
        with st.expander("🖥️ Logs de debug (Nmax / tentatives)", expanded=False):
            st.code("\n".join(st.session_state["debug_logs"]))
        # Bouton pour lire le fichier log complet si accessible
        try:
            with open("/tmp/sequencage_debug.log") as f:
                log_content = f.read()
            st.download_button("📥 Télécharger le log complet", log_content,
                               file_name="debug_nmax.log", mime="text/plain")
        except Exception:
            pass

    # ── Affichage persistant des SJ non traités ─────────────────────────────
    sj_nt_global = st.session_state.get("sj_non_traites_par_jour", {})
    if sj_nt_global:
        st.divider()
        st.subheader("⚠️ SuperJobs non traités")
        for jour_nt, sj_liste in sj_nt_global.items():
            with st.expander(f"🔍 {jour_nt} — {len(sj_liste)} SuperJob(s) non traité(s)", expanded=True):
                for sj in sj_liste:
                    h_dispo    = sj.h_dispo_min
                    h_deadline = min(to_min(j.h_deadline) for j in sj.liste_jobs)
                    st.markdown(
                        f"**SJ flux {sj.liste_jobs[0].flux_id}** | "
                        f"🚛 {sj.v_type} | "
                        f"⏰ {int(h_dispo//60):02d}h{int(h_dispo%60):02d}"
                        f" → {int(h_deadline//60):02d}h{int(h_deadline%60):02d} | "
                        f"⏱️ {sj.poids_total:.0f} min"
                    )
                    rows_nt = []
                    for j in sj.liste_jobs:
                        orig = getattr(j, 'origin', getattr(j, 'origine', '?'))
                        dest = getattr(j, 'destination', '?')
                        qte  = getattr(j, 'quantite', getattr(j, 'nb_contenants', '?'))
                        cont = getattr(j, 'type_contenant', '')
                        h_d  = to_min(j.h_dispo)
                        h_dl = to_min(j.h_deadline)
                        rows_nt.append({
                            'Origine'    : orig,
                            'Destination': dest,
                            'Contenant'  : cont,
                            'Qté'        : qte,
                            'H dispo'    : f"{int(h_d//60):02d}h{int(h_d%60):02d}",
                            'H deadline' : f"{int(h_dl//60):02d}h{int(h_dl%60):02d}",
                        })
                    st.dataframe(pd.DataFrame(rows_nt), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("3️⃣ Détail opérationnel par jour")

    jours_dispo = st.session_state.get('jours_simules', [])
    if not jours_dispo:
        st.stop()

    jour_sel = st.selectbox("Choisir un jour à détailler", jours_dispo)
    postes_jour  = st.session_state['dict_postes_par_jour'].get(jour_sel, [])
    liste_sj_sel = st.session_state['dict_detail_sj'].get(jour_sel, [])

    if not postes_jour:
        st.info("Aucun résultat pour ce jour.")
        st.stop()

    # ── Gantt global (tous véhicules) ─────────────────────────────────────
    st.markdown(f"#### 📅 Planning complet — {jour_sel}")
    types_dispo = sorted({p.vehicule_type for p in postes_jour})
    type_filtre = st.selectbox("Filtrer par type de véhicule",
                               ["Tous"] + types_dispo, key="filtre_type")
    postes_affiches = postes_jour if type_filtre == "Tous" else [
        p for p in postes_jour if p.vehicule_type == type_filtre]

    res_flux.afficher_gantt_chauffeur_detaille(postes_affiches, 
        type_filtre if type_filtre != "Tous" else postes_affiches[0].vehicule_type,
        liste_sj_sel)

    # ── Onglets Détail par poste / Détail tournées ───────────────────────
    st.divider()
    tab_postes, tab_sites = st.tabs(["🔍 Détail par poste", "🏥 Détail tournées par site"])

    def min_to_h(m):
        return f"{int(m // 60):02d}h{int(m % 60):02d}"

    # ════════════════════════════════════════════════════════════════════
    # TAB 1 — DÉTAIL PAR POSTE
    # ════════════════════════════════════════════════════════════════════
    with tab_postes:
        for p in postes_jour:  # tous les postes, pas seulement le filtre véhicule
            hist_tri = sorted(p.historique, key=lambda x: x["Minute_Debut"])

            # Découper l'historique en intervalles PRISE_POSTE → PASSATION_FIN
            # Un poste peut avoir plusieurs services dans la journée (AM/PM)
            services = []
            service_en_cours = None
            for ev in hist_tri:
                if ev["Activite"] == "PRISE_POSTE":
                    service_en_cours = {"debut": ev, "events": [ev], "fin": None}
                elif ev["Activite"] in ("PASSATION_FIN", "FIN_DE_SERVICE") and service_en_cours:
                    service_en_cours["events"].append(ev)
                    service_en_cours["fin"] = ev
                    services.append(service_en_cours)
                    service_en_cours = None
                elif service_en_cours:
                    service_en_cours["events"].append(ev)
            # Service sans fin explicite (poste encore actif)
            if service_en_cours:
                services.append(service_en_cours)

            if not services:
                continue

            for idx_s, service in enumerate(services):
                evs = service["events"]
                h_deb_s  = service["debut"]["Heure_Debut"]
                m_deb_s  = service["debut"]["Minute_Debut"]
                h_fin_s  = service["fin"]["Heure_Debut"] if service["fin"] else evs[-1]["Heure_Debut"]
                m_fin_s  = service["fin"]["Minute_Debut"] if service["fin"] else evs[-1]["Minute_Debut"]
                duree_service = max(m_fin_s - m_deb_s, 1)
                nb_missions = sum(1 for e in evs if e["Activite"] == "EN_MISSION")

                # ── Calcul des temps par catégorie ──────────────────────────
                ACTI_MISSION  = {"EN_MISSION"}
                ACTI_TRAJET   = {"EN_TRAJET_VIDE", "RETOUR_DEPOT", "EN_RETOUR_DEPOT"}
                ACTI_DISPO    = {"DISPONIBLE", "INTER_JOB"}

                def duree_ev(i_ev):
                    debut_ev = evs[i_ev]["Minute_Debut"]
                    fin_ev   = evs[i_ev + 1]["Minute_Debut"] if i_ev < len(evs) - 1 else debut_ev + 15
                    return max(0, fin_ev - debut_ev)

                t_mission = sum(duree_ev(i) for i, e in enumerate(evs) if e["Activite"] in ACTI_MISSION)
                t_trajet  = sum(duree_ev(i) for i, e in enumerate(evs) if e["Activite"] in ACTI_TRAJET)
                t_dispo   = sum(duree_ev(i) for i, e in enumerate(evs) if e["Activite"] in ACTI_DISPO)
                taux_occ  = round((1 - t_dispo / duree_service) * 100, 1)

                # Trajet chargé = somme des étapes TRAJET dans les chronologies des missions
                sj_index_p = {sj.super_job_id: sj for sj in liste_sj_sel}
                t_trajet_charge = 0
                for ev_m in evs:
                    if ev_m["Activite"] != "EN_MISSION" or ev_m.get("SJ_ID", "N/A") == "N/A":
                        continue
                    sj_m = sj_index_p.get(ev_m["SJ_ID"])
                    if sj_m and hasattr(sj_m, "chronologie"):
                        t_trajet_charge += sum(
                            e["duree"] for e in sj_m.chronologie
                            if e["action"] == "TRAJET"
                        )
                t_roulage = t_trajet_charge + t_trajet  # chargé + à vide
                ratio_charge = round(t_trajet_charge / t_roulage * 100, 1) if t_roulage > 0 else 0

                with st.expander(
                    f"🚛 {p.id_poste}  —  Service {idx_s + 1}  |  {h_deb_s} → {h_fin_s}  "
                    f"|  {nb_missions} mission(s)  |  Taux occup. {taux_occ}%",
                    expanded=False
                ):
                    # ── Métriques synthétiques ──────────────────────────────
                    c1, c2, c3, c4, c5 = st.columns(5)
                    c1.metric("⏱ En mission",          f"{t_mission} min")
                    c2.metric("🚗 Trajet à vide",       f"{t_trajet} min")
                    c3.metric("⏳ Dispo sans tâche",    f"{t_dispo} min")
                    c4.metric("📊 Taux d'occupation",   f"{taux_occ} %")
                    c5.metric("📦 Trajet chargé/roulage", f"{ratio_charge} %",
                              help=f"Trajet chargé : {t_trajet_charge} min — Roulage total : {t_roulage} min")
                    st.divider()

                    rows = []

                    # ── Capacité max du véhicule pour la heatmap ─────────────
                    cap_max_cont = None
                    for ev_cap in evs:
                        if ev_cap.get("SJ_ID", "N/A") != "N/A":
                            sj_cap = next((s for s in liste_sj_sel
                                           if s.super_job_id == ev_cap["SJ_ID"]), None)
                            if sj_cap and sj_cap.liste_jobs:
                                j0 = sj_cap.liste_jobs[0]
                                if j0.taux_occupation > 0:
                                    cap_max_cont = round(j0.quantite / j0.taux_occupation)
                                    break

                    for i, ev in enumerate(evs):
                        acti = ev["Activite"]
                        debut  = ev["Minute_Debut"]
                        fin_m  = evs[i + 1]["Minute_Debut"] if i < len(evs) - 1 else debut + 15
                        h_fin_ev = min_to_h(fin_m)

                        if acti == "EN_MISSION":
                            sj_id = ev.get("SJ_ID", "N/A")
                            sj = next((s for s in liste_sj_sel if s.super_job_id == sj_id), None)
                            if sj and hasattr(sj, "chronologie") and sj.chronologie:
                                # En-tête mission
                                rows.append({
                                    "Heure début" : ev["Heure_Debut"],
                                    "Heure fin"   : h_fin_ev,
                                    "Activité"    : f"── {sj.type_logistique} {sj_id} ──",
                                    "Détail"      : "",
                                    "À bord"      : "",
                                    "_bord_val"   : None,
                                })
                                cont_a_bord = 0
                                for etape in sj.chronologie:
                                    h_deb_e = min_to_h(debut + etape["t_debut"])
                                    h_fin_e = min_to_h(debut + etape["t_fin"])
                                    qte = etape.get("quantite", 0)
                                    if etape["action"] == "CHARGEMENT":
                                        cont_a_bord += qte
                                    elif etape["action"] == "DECHARGEMENT":
                                        cont_a_bord = max(0, cont_a_bord - qte)
                                    if cap_max_cont and cap_max_cont > 0:
                                        taux = round(cont_a_bord / cap_max_cont * 100)
                                        bord_str = f"{int(cont_a_bord)} ({taux}%)"
                                        bord_val = cont_a_bord / cap_max_cont
                                    else:
                                        bord_str = str(int(cont_a_bord)) if cont_a_bord > 0 else ""
                                        bord_val = None
                                    rows.append({
                                        "Heure début" : h_deb_e,
                                        "Heure fin"   : h_fin_e,
                                        "Activité"    : etape["action"],
                                        "Détail"      : etape.get("label", ""),
                                        "À bord"      : bord_str,
                                        "_bord_val"   : bord_val,
                                    })
                            elif sj:
                                rows.append({
                                    "Heure début" : ev["Heure_Debut"],
                                    "Heure fin"   : h_fin_ev,
                                    "Activité"    : "EN_MISSION",
                                    "Détail"      : " / ".join(
                                        f"{getattr(j,'origin','?')}"
                                        f" → {getattr(j,'destination','?')}"
                                        f" ({getattr(j,'quantite','?')} cont.)"
                                        for j in sj.liste_jobs),
                                    "À bord"      : "",
                                    "_bord_val"   : None,
                                })

                        elif acti == "INTER_JOB":
                            rows.append({
                                "Heure début" : ev["Heure_Debut"],
                                "Heure fin"   : h_fin_ev,
                                "Activité"    : acti,
                                "Détail"      : f"Position : {ev.get('position_depart','?')}",
                                "À bord"      : "",
                                "_bord_val"   : None,
                            })

                        elif acti in ("EN_TRAJET_VIDE", "RETOUR_DEPOT", "EN_RETOUR_DEPOT"):
                            pt_dep = ev.get("position_depart", "?")
                            if acti == "EN_TRAJET_VIDE" and ev.get("SJ_ID") != "N/A":
                                sj = next((s for s in liste_sj_sel
                                           if s.super_job_id == ev.get("SJ_ID")), None)
                                pt_arr = sj.points_depart[0] if sj else p.stationnement_initial
                            else:
                                pt_arr = p.stationnement_initial
                            rows.append({
                                "Heure début" : ev["Heure_Debut"],
                                "Heure fin"   : h_fin_ev,
                                "Activité"    : acti,
                                "Détail"      : f"{pt_dep} → {pt_arr}",
                                "À bord"      : "",
                                "_bord_val"   : None,
                            })

                        elif acti in ("EN_PAUSE", "PRISE_POSTE", "PASSATION_FIN", "FIN_DE_SERVICE"):
                            rows.append({
                                "Heure début" : ev["Heure_Debut"],
                                "Heure fin"   : h_fin_ev,
                                "Activité"    : acti,
                                "Détail"      : ev.get("Details", ""),
                                "À bord"      : "",
                                "_bord_val"   : None,
                            })

                    if rows:
                        df_rows = pd.DataFrame(rows)
                        bord_vals = df_rows["_bord_val"].tolist()
                        df_display = df_rows.drop(columns=["_bord_val"])

                        def color_bord(row):
                            styles = [""] * len(row)
                            idx_col = list(df_display.columns).index("À bord")
                            bv = bord_vals[row.name] if row.name < len(bord_vals) else None
                            if bv is not None and bv > 0:
                                r_c = int(min(255, bv * 2 * 255))
                                g_c = int(min(255, (1 - bv) * 2 * 255))
                                styles[idx_col] = (
                                    f"background-color: rgb({r_c},{g_c},50);"
                                    f" color: white; font-weight: bold"
                                )
                            return styles

                        st.dataframe(
                            df_display.style.apply(color_bord, axis=1),
                            use_container_width=True, hide_index=True
                        )


    # ════════════════════════════════════════════════════════════════════
    # TAB 2 — DÉTAIL TOURNÉES PAR SITE
    # Source unique : les lignes du tableau par poste (rows déjà construits)
    # On réexploite exactement les mêmes données que tab_postes.
    # ════════════════════════════════════════════════════════════════════
    with tab_sites:

        # ── Reconstruire les passages depuis l'historique des postes ─────
        # On rejoue exactement la même logique que tab_postes pour les
        # étapes CHARGEMENT / DECHARGEMENT, sans passer par liste_sj_sel.
        sj_index_ts = {sj.super_job_id: sj for sj in liste_sj_sel}
        tous_sites_ts = set()
        all_passages  = []  # tous sites confondus

        for p in postes_jour:
            hist_tri_p = sorted(p.historique, key=lambda x: x["Minute_Debut"])
            for i_ev, ev in enumerate(hist_tri_p):
                if ev["Activite"] != "EN_MISSION" or ev.get("SJ_ID", "N/A") == "N/A":
                    continue
                sj = sj_index_ts.get(ev["SJ_ID"])
                if not sj or not hasattr(sj, "chronologie") or not sj.chronologie:
                    continue
                t_ancre = ev["Minute_Debut"]

                for etape in sj.chronologie:
                    action = etape["action"]
                    if action not in ("CHARGEMENT", "DECHARGEMENT"):
                        continue
                    site = etape["site"]
                    tous_sites_ts.add(site)
                    sens = "PRISE" if action == "CHARGEMENT" else "DEPOSE"

                    # Arrivée quai = étape MISE_A_QUAI précédente (index 0-based)
                    h_arrivee_quai = "—"
                    idx_e = etape["etape"] - 2
                    if 0 <= idx_e < len(sj.chronologie):
                        ep = sj.chronologie[idx_e]
                        if "MISE_A_QUAI" in ep["action"]:
                            h_arrivee_quai = min_to_h(t_ancre + ep["t_debut"])

                    h_debut_op = min_to_h(t_ancre + etape["t_debut"])
                    h_fin_op   = min_to_h(t_ancre + etape["t_fin"])

                    job_ids = etape.get("job_id", [])
                    if isinstance(job_ids, str):
                        job_ids = [job_ids]
                    jobs_concernes = [j for j in sj.liste_jobs if j.job_id in job_ids]

                    if jobs_concernes:
                        contenu = " + ".join(
                            f"{j.quantite} {j.contenant} → {j.destination}"
                            if sens == "PRISE" else
                            f"{j.quantite} {j.contenant} (depuis {j.origin})"
                            for j in jobs_concernes
                        )
                    else:
                        contenu = etape.get("label", "")

                    all_passages.append({
                        "site"           : site,
                        "Poste"          : p.id_poste,
                        "Arrivée quai"   : h_arrivee_quai,
                        "Début opération": h_debut_op,
                        "Fin opération"  : h_fin_op,
                        "PRISE"          : contenu if sens == "PRISE" else "",
                        "DEPOSE"         : contenu if sens == "DEPOSE" else "",
                        "_sort"          : t_ancre + etape["t_debut"],
                    })

        tous_sites_ts = sorted(tous_sites_ts)

        if not tous_sites_ts:
            st.info("Aucune donnée de chronologie disponible.")
        else:
            site_sel = st.selectbox("Sélectionner un site", tous_sites_ts, key="sel_site")
            passages = [r for r in all_passages if r["site"] == site_sel]
            passages.sort(key=lambda x: x["_sort"])

            if passages:
                df_site = pd.DataFrame(passages).drop(columns=["site", "_sort"])
                st.dataframe(df_site, use_container_width=True, hide_index=True)
            else:
                st.info(f"Aucun passage trouvé pour {site_sel}.")
