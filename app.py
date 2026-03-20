import streamlit as st
from streamlit_option_menu import option_menu
from pathlib import Path

from modules.Import import show_import

#__ajout 18/03 LM
from modules.dataViz import show_flux_control_charts
#__fin ajout

#__ajout LM 16/03
from modules.GeoMatrix import run_matrix_tool
#__fin ajout

#__ajout BG 19/03
from modules.biologie_engine import run_optimization
import folium
from streamlit_folium import st_folium
import pandas as pd
#__ajout BG 19/03

try:
    from modules.dataViz import show_volumes, show_biologie
except ImportError:
    show_volumes = None
    show_biologie = None


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

# Ajout BG 19/03
def show_biologie_page():
    st.title("🧪 Paramétrage des Passages Biologie")

    if "data" not in st.session_state:
        st.warning("⚠️ Veuillez d'abord importer un fichier Excel.")
        return

    data = st.session_state["data"]
    df_sites = data["accessibilite_sites"]
    
    # Paramètres globaux
    col_g1, col_g2 = st.columns(2)
    with col_g1:
        duree_max = st.number_input("Durée max tournée (min)", value=200)
    with col_g2:
        temps_coll = st.number_input("Temps de collecte (min)", value=10)

    st.divider()
    st.subheader("🏥 Sélection et configuration des sites")

    # Initialisation du dictionnaire de configuration
    current_sites_config = {}

    for index, row in df_sites.iterrows():
        site_name = row['site']
        if site_name == "HLS": continue 
        
        # Création d'une ligne avec checkbox et expander
        cols = st.columns([1, 4])
        
        # 1. Possibilité de cocher/décocher le site
        is_active = cols[0].checkbox("Inclure", value=True, key=f"check_{site_name}")
        
        if is_active:
            with cols[1].expander(f"📍 {site_name}", expanded=True):
                c1, c2 = st.columns([3, 1])
                with c1:
                    # Slider pour la plage horaire
                    # On peut pré-remplir avec les valeurs de l'Excel si vous les avez extraites
                    res = st.select_slider(
                        f"Plage horaire",
                        options=range(0, 1441, 15),
                        value=(480, 1080), # 8h - 18h par défaut
                        format_func=lambda x: f"{x//60:02d}:{x%60:02d}",
                        key=f"slide_{site_name}"
                    )
                with c2:
                    freq = st.number_input(f"Fréquence", min_value=1, value=5, key=f"freq_{site_name}")

                # On n'ajoute à la config que si le site est coché
                current_sites_config[site_name] = {
                    'open': res[0],
                    'close': res[1],
                    'freq': freq
                }
        else:
            cols[1].info(f"❄️ {site_name} est exclu de la simulation.")

    # Sauvegarde
    if st.button("💾 Enregistrer la configuration", use_container_width=True):
        st.session_state["biologie_config"] = {
            "duree_max": duree_max,
            "temps_collecte": temps_coll,
            "sites": current_sites_config
        }
        st.success(f"Configuration enregistrée : {len(current_sites_config)} sites actifs.")
# fin ajout

# ajout fonction affichage des résultant. 
def show_detail_tournees():
    st.title("📋 Détail des Tournées Biologie")

    # 1. VÉRIFICATION DES DONNÉES
    if "resultat_flotte" not in st.session_state or not st.session_state.resultat_flotte:
        st.warning("⚠️ Aucun résultat disponible. Veuillez lancer l'optimisation dans l'onglet 'Optimisation'.")
        return

    flotte = st.session_state.resultat_flotte
    # On récupère la matrice pour les calculs de distance
    df_matrice = st.session_state["data"]["matrice_duree"].copy()
    
    # Nettoyage de la matrice pour la recherche (Index et Colonnes en MAJUSCULES)
    df_matrice.index = df_matrice.index.astype(str).str.strip().str.upper()
    df_matrice.columns = df_matrice.columns.astype(str).str.strip().str.upper()

    # --- 2. TABLEAU SYNTHÈSE VÉHICULES ---
    st.subheader("📊 Performance de la Flotte")
    
    summary_data = []
    for i, v_tours in enumerate(flotte):
        # Heures (en minutes depuis minuit)
        h_debut_min = v_tours[0][0]['heure']
        h_fin_min = v_tours[-1][-1]['heure']
        
        # Calcul de la distance totale (km)
        dist_totale = 0
        nb_arrets = 0
        for tour in v_tours:
            nb_arrets += len(tour)
            for j in range(len(tour) - 1):
                loc_a = str(tour[j]['site']).strip().upper()
                loc_b = str(tour[j+1]['site']).strip().upper()
                if loc_a in df_matrice.index and loc_b in df_matrice.columns:
                    dist_totale += df_matrice.loc[loc_a, loc_b]
        
        # Taux d'occupation (basé sur une journée de 8h = 480 min)
        amplitude = h_fin_min - h_debut_min
        taux_occ = (amplitude / 480) * 100
        
        summary_data.append({
            "Véhicule": f"Véhicule {i+1}",
            "Début": f"{int(h_debut_min//60):02d}:{int(h_debut_min%60):02d}",
            "Fin": f"{int(h_fin_min//60):02d}:{int(h_fin_min%60):02d}",
            "Distance (km)": round(dist_totale, 1),
            "Nb Tournées": len(v_tours),
            "Taux d'occ (%)": f"{min(100, round(taux_occ, 1))}%"
        })

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
    options = ["Accueil", "Calcul Matrices", "Importer Données", "Volumes Distribution", "Passages Biologie", "Simuler & Optimiser"]
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
    if "data" in st.session_state:
        show_flux_control_charts()
        #__fin ajout
elif selected == "Volumes Distribution":
    show_volumes_page()
elif selected == "Passages Biologie":
    show_biologie_page()
elif selected == "Simuler & Optimiser":
    show_simulation_page()
elif selected == "Synthèse":
    st.title("📊 Synthèse des résultats")
elif selected == "Détail tournées":
    show_detail_tournees()
elif selected == "Exporter":
    st.title("📥 Exporter les résultats")
