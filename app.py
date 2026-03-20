import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path
import folium
import pandas as pd
import plotly.express as px


from modules.Import import show_import
from modules.dataViz import show_flux_control_charts
from modules.GeoMatrix import run_matrix_tool
from modules.biologie_engine import run_optimization
from streamlit_folium import st_folium
from modules.bioViz import calculate_kpis, render_fleet_gantt, render_site_passages, render_tournee_map
from modules.param_bio import show_biologie_page
#from modules.dataViz import show_volumes, show_biologie



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


# ajout fonction affichage des résultant. 
def show_detail_tournees():
    if "resultat_flotte" not in st.session_state:
        st.info("Aucun résultat à afficher.")
        return

    flotte = st.session_state.resultat_flotte  # C'est maintenant un dictionnaire
    
    st.subheader("📊 Synthèse Moyens Mobiles")
    
    summary = []
    total_chauffeurs = 0
    
    for v_id, postes in flotte.items():
        n_chauffeurs = len(postes)
        total_chauffeurs += n_chauffeurs
        
        # Calcul distance totale du véhicule
        dist_v = 0
        # ... (votre logique de calcul de distance parcourue par le véhicule)
        
        summary.append({
            "Moyen de Transport": v_id,
            "Nombre de Chauffeurs (Relèves)": n_chauffeurs,
            "Amplitude Totale": f"{int(postes[0][0][0]['heure']//60):02d}h - {int(postes[-1][-1][-1]['heure']//60):02d}h",
            "Statut": "Optimisé"
        })

    # Affichage de KPIs rapides
    c1, c2 = st.columns(2)
    c1.metric("🚗 Véhicules mobilisés", len(flotte))
    c2.metric("👨‍✈️ Chauffeurs nécessaires", total_chauffeurs)

    st.dataframe(pd.DataFrame(summary), use_container_width=True, hide_index=True)

    # --- SÉLECTEUR POUR LA CARTE ---
    st.divider()
    st.subheader("🗺️ Détail des vacations")
    
    v_sel = st.selectbox("Choisir un véhicule", list(flotte.keys()))
    p_idx = st.selectbox("Choisir le chauffeur / vacation", 
                         range(len(flotte[v_sel])), 
                         format_func=lambda x: f"Chauffeur n°{x+1}")
    
    vacation = flotte[v_sel][p_idx] # Liste de tournées du chauffeur choisi
    
    # Affichage de la frise pour ce chauffeur spécifique
    for i, tournee in enumerate(vacation):
        with st.expander(f"Tournée {i+1}"):
            for s in tournee:
                st.write(f"📍 {int(s['heure']//60):02d}:{int(s['heure']%60):02d} - {s['site']}")

    st.dataframe(pd.DataFrame(summary_data), use_container_width=True, hide_index=True)

    st.divider()

    # --- 3. SÉLECTION ET CARTE ---
    st.subheader("🔍 Analyse détaillée par trajet")
    
    c1, c2 = st.columns(2)
    with c1:
        v_idx = st.selectbox("Sélectionner un véhicule", range(len(flotte)), 
                             format_func=lambda x: f"Véhicule {x+1}")
    with c2:
        # On filtre les tournées du véhicule choisi
        tournees_dispo = flotte[v_idx]
        t_idx = st.selectbox("Sélectionner la tournée", range(len(tournees_dispo)), 
                             format_func=lambda x: f"Tournée n°{x+1}")

    tournee_actuelle = tournees_dispo[t_idx]

    # --- 4. AFFICHAGE CARTE ET FEUILLE DE ROUTE ---
    col_map, col_list = st.columns([2, 1])

    with col_list:
        st.markdown(f"**Feuille de route - T{t_idx+1}**")
        for step in tournee_actuelle:
            h = step['heure']
            st.write(f"🕒 **{int(h//60):02d}:{int(h%60):02d}** : {step['site']}")

    with col_map:
        # On vérifie si on a les coordonnées pour la carte
        if "coords_sites" in st.session_state:
            coords = st.session_state.coords_sites
            
            # Centre la carte sur le premier point
            first_site = tournee_actuelle[0]['site'].strip().upper()
            m = folium.Map(location=[coords[first_site]['lat'], coords[first_site]['lon']], zoom_start=12)
            
            points_gps = []
            for step in tournee_actuelle:
                s_name = step['site'].strip().upper()
                if s_name in coords:
                    lat, lon = coords[s_name]['lat'], coords[s_name]['lon']
                    points_gps.append([lat, lon])
                    
                    # Marqueur
                    h = step['heure']
                    label = f"{s_name} ({int(h//60):02d}:{int(h%60):02d})"
                    folium.Marker(
                        [lat, lon], 
                        popup=label, 
                        tooltip=label,
                        icon=folium.Icon(color="red" if s_name == "HLS" else "blue", icon="info-sign")
                    ).add_to(m)
            
            # Tracer la ligne du trajet
            if len(points_gps) > 1:
                folium.PolyLine(points_gps, color="blue", weight=3, opacity=0.8).add_to(m)
            
            st_folium(m, width=None, height=400)
        else:
            st.info("ℹ️ Pour afficher la carte, assurez-vous d'avoir géocodé les adresses (coords_sites).")

def afficher_frise_par_site():
    st.subheader("⏱️ Chronologie des passages par site")
    
    if "resultat_flotte" not in st.session_state:
        st.info("Lancez d'abord la simulation.")
        return

    flotte = st.session_state.resultat_flotte
    
    # 1. Extraction de TOUS les passages pour TOUS les sites
    all_passages = []
    for i, v_tours in enumerate(flotte):
        for tour in v_tours:
            for step in tour:
                # On ignore le dépôt HLS pour la frise des sites périphériques
                if step['site'].upper() != "HLS":
                    all_passages.append({
                        "Site": step['site'].upper(),
                        "Heure": step['heure'],
                        "Horaire": f"{int(step['heure']//60):02d}:{int(step['heure']%60):02d}",
                        "Véhicule": f"Véhicule {i+1}"
                    })
    
    df_passages = pd.DataFrame(all_passages)
    
    if df_passages.empty:
        st.warning("Aucun passage hors HLS détecté.")
        return

    # 2. Menu déroulant pour choisir le site
    liste_sites = sorted(df_passages["Site"].unique())
    site_sel = st.selectbox("Choisir un site pour voir ses passages", liste_sites)
    
    df_site = df_passages[df_passages["Site"] == site_sel].copy()

    # 3. Création du graphique Plotly (Scatter plot sur un seul axe Y)
    fig = px.scatter(
        df_site, 
        x="Heure", 
        y=[site_sel] * len(df_site), # Tous les points sur la même ligne
        color="Véhicule",
        hover_data={"Heure": False, "Horaire": True, "Véhicule": True},
        title=f"Passages prévus à {site_sel}",
        labels={"x": "Heure de la journée", "y": ""},
        color_discrete_sequence=px.colors.qualitative.Safe # Couleurs distinctes
    )

    # Personnalisation de l'axe X pour afficher des heures (08:00, 10:00...)
    fig.update_xaxes(
        tickmode='array',
        tickvals=list(range(480, 1200, 60)), # De 8h à 20h
        ticktext=[f"{h//60:02d}:00" for h in range(480, 1200, 60)],
        range=[450, 1150]
    )
    
    fig.update_traces(marker=dict(size=15, symbol='diamond'))
    fig.update_layout(height=250, showlegend=True)

    st.plotly_chart(fig, use_container_width=True)

# FIN AJOUT

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
    options = ["Accueil", "Calcul Matrices", "Importer Données", "Volumes Distribution", "🧪 Passages Biologie", "Simuler & Optimiser"]
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
# --- Modif LM 20/03: Suppression du contrôle visuel des flux sous l'import ---
    #if "data" in st.session_state:
     #   show_flux_control_charts()
        #__fin ajout

# --- Ajout LM 20/03: Contrôle visuel des flux dans l'onglet volumes flux ---
elif selected == "Volumes Distribution":
    if "data" in st.session_state:
        # On appelle la fonction de dataViz.py ici
        show_flux_control_charts()
    else:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel dans l'onglet 'Importer Données'.")
        #__fin ajout
elif selected == "🧪 Passages Biologie":
    show_biologie_page()
elif selected == "Simuler & Optimiser":
    show_simulation_page()
elif selected == "Synthèse":
    st.title("📊 Synthèse des résultats")
elif selected == "Détail tournées":
    show_detail_tournees()
    afficher_frise_par_site()
elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
