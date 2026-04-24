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
    tunnel_consolidation_flux       # Ajoutez celle-ci
)
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
        matrice_duree = st.session_state['data'].get('matrice_duree')

        # Petite fonction de secours pour l'affichage des heures
        def fmt_heure_safe(val):
            try:
                if pd.isna(val) or val is None: return "--:--"
                if isinstance(val, str): return val
                h = int(val // 60)
                m = int(val % 60)
                return f"{h:02d}:{m:02d}"
            except:
                return "Err"

        if st.button("🚀 Lancer la simulation hebdomadaire", type="primary", use_container_width=True):
            if matrice_duree is None:
                st.error("⚠️ Matrice de temps introuvable.")
            else:
                try:
                    jours_semaine = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
                    resultats_hebdo = []
                    detail_par_jour = {} 
                    
                    with st.status("Simulation de la semaine en cours...", expanded=True) as status:
                        from modules.sim_engine import preparer_flux_complets_du_jour, tunnel_consolidation_flux
                        
                        for jour in jours_semaine:
                            st.write(f"⏳ Analyse du **{jour}**...")
                            df_complet_jour = preparer_flux_complets_du_jour(df_recurrent, df_specifique, jour)
                            
                            liste_globale_sj = tunnel_consolidation_flux(
                                df_complet_jour, df_vehicules, df_contenants, df_sites, matrice_duree
                            )
                            
                            detail_par_jour[jour] = liste_globale_sj


                            # Calcul du pic de charge pour le récapitulatif
                            n_max_j, intensite_j = calculer_nmax_theorique(liste_globale_sj)
                            comptage_jour = {"Jour": jour, "Nmax": n_max_j}
                            
                            total_temps_jour = 0
                            for sj in liste_globale_sj:
                                v_type = sj.v_type
                                col_name = f"SJ - {v_type}"
                                comptage_jour[col_name] = comptage_jour.get(col_name, 0) + 1
                                total_temps_jour += sj.poids_total
                            
                            comptage_jour["Temps Total (h)"] = round(total_temps_jour / 60, 1)
                            resultats_hebdo.append(comptage_jour)
                        
                        st.session_state['df_recap_hebdo'] = pd.DataFrame(resultats_hebdo).fillna(0)
                        st.session_state['dict_detail_sj'] = detail_par_jour
                        status.update(label="✅ Simulation terminée !", state="complete")
                except Exception as e:
                    st.error(f"Erreur simulation : {e}")

        if 'df_recap_hebdo' in st.session_state:
            st.divider()
            st.subheader("📊 Récapitulatif Hebdomadaire")
            st.dataframe(st.session_state['df_recap_hebdo'], use_container_width=True)

            st.divider()
            st.subheader("🔍 Détail opérationnel par jour")
            jour_sel = st.selectbox("Choisir un jour :", ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"])
            
            liste_sj_jour = st.session_state['dict_detail_sj'].get(jour_sel, [])
            
            if liste_sj_jour:
                # --- ÉTAPE 1 : CALCUL VENTILÉ PAR TYPE (Nouvelle Logique) ---
                from modules.sim_engine import calculer_nmax_par_type
                
                # On récupère le dictionnaire {Type: [48 créneaux]}
                intensite_dict = calculer_nmax_par_type(liste_sj_jour)
                
                st.write(f"**📈 Courbe de charge ventilée du {jour_sel}**")
                
                # Préparation du DataFrame pour le graphique empilé
                labels_h = [f"{int(i*30//60):02d}:{(i*30)%60:02d}" for i in range(48)]
                df_graph = pd.DataFrame(intensite_dict, index=labels_h)
                
                # L'affichage devient un graphique d'aire empilé par couleur
                st.area_chart(df_graph)

                # --- ÉTAPE 2 : INDICATEURS NMAX PAR TYPE ---
                st.write("**Besoin max détecté par catégorie :**")
                cols_nmax = st.columns(len(intensite_dict))
                for i, (v_type, intensites) in enumerate(intensite_dict.items()):
                    pic = max(intensites)
                    n_max_v = math.ceil(pic * 1.20)
                    with cols_nmax[i]:
                        st.metric(f"Nmax {v_type}", f"{n_max_v} véh.")
                
                # Tableau des SuperJobs
                recap_sj = []
                for i, sj in enumerate(liste_sj_jour):
                    recap_sj.append({
                        "Camion ID": f"{jour_sel[:3]}_{i+1:02d}",
                        "Type Véhicule": sj.liste_jobs[0].vehicule_type,
                        "Taux Remplissage": f"{round(sj.taux_occupation_total * 100, 1)}%",
                        "Nb Flux": len(sj.liste_jobs),
                        "Temps (min)": int(sj.calculer_poids_mobilisation()),
                        "Taux_Brut": sj.taux_occupation_total # Caché pour le tri
                    })
                df_sj = pd.DataFrame(recap_sj)
                st.dataframe(df_sj.drop(columns=['Taux_Brut']), use_container_width=True)

                # --- FOCUS SUR LES 10 PIRES REMPLISSAGES ---
                st.subheader(f"⚠️ Focus : Les 10 camions les moins remplis du {jour_sel}")
                top_10_pires = df_sj.nsmallest(10, 'Taux_Brut')
                
                details_top = []
                for _, row in top_10_pires.iterrows():
                    # On retrouve l'objet SuperJob original via son index
                    idx_original = int(row['Camion ID'].split('_')[1]) - 1
                    sj_obj = liste_sj_jour[idx_original]
                    
                    for job in sj_obj.liste_jobs:
                        details_top.append({
                            "Camion": row['Camion ID'],
                            "Occupation": row['Taux Remplissage'],
                            "Origine": job.origin,
                            "Destination": job.destination,
                            "Dispo": fmt_heure_safe(job.h_dispo),
                            "Deadline": fmt_heure_safe(job.h_deadline),
                            "Qté": job.quantite,
                            "Type": job.type_propre_sale
                        })
                
                st.dataframe(pd.DataFrame(details_top), use_container_width=True)
            else:
                st.info("Aucune donnée pour ce jour.")
    else:
        st.warning("⚠️ Veuillez générer la 'Séquence Type' avant de lancer cette synthèse.")


    if st.session_state.get('planning_detaille'):
        postes = st.session_state['planning_detaille']
        
        st.write("## 📅 Planning graphique")
        
        # Sélecteur de type de véhicule (car on a segmenté l'ordonnancement)
        liste_types = sorted(list(set(p['v_type'] for p in postes)))
        type_choisi = st.selectbox("Sélectionnez le type de flotte à visualiser :", liste_types)
        
        # Appel de la fonction du module
        res_flux.afficher_gantt_chauffeur_detaille(postes, type_choisi)


    else:
        st.info("⚠️ Aucune simulation n'est en mémoire. Allez dans l'onglet 'Simul tournées' et cliquez sur Lancer.")
