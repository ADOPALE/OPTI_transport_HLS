#__Création LM 16/03

import streamlit as st
import pandas as pd
import numpy as np
import requests
import time
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
from io import BytesIO

# ==========================================
# PARTIE 1 : LOGIQUE MÉTIER (Calculs)
# ==========================================

def get_coordinates(df_input, progress_bar, status_text):
    """
    Géocode les adresses uniques.
    Retourne un dictionnaire {adresse: (lat, lon)}
    """
    geolocator = Nominatim(user_agent="adopale_hospital_sim")
    # RateLimiter respecte la politique d'OSM (1 requête/sec)
    geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.1)
    
    unique_addresses = df_input['adresse'].unique()
    cache_coords = {}
    total = len(unique_addresses)

    for i, addr in enumerate(unique_addresses):
        status_text.text(f"🌍 Géocodage : {i+1} / {total} adresses")
        progress_bar.progress((i + 1) / total)
        
        try:
            location = geocode(addr)
            if location:
                cache_coords[addr] = (location.latitude, location.longitude)
            else:
                cache_coords[addr] = (None, None)
        except Exception:
            cache_coords[addr] = (None, None)
            
    return cache_coords

def calculate_osrm_matrices(df_sites, progress_bar, status_text):
    """
    Calcule les matrices via l'API OSRM.
    Gère la règle métier : même adresse = 0.
    """
    n = len(df_sites)
    dist_matrix = np.zeros((n, n))
    dur_matrix = np.zeros((n, n))
    
    # Extraction des coordonnées pour l'API (format lon,lat)
    coords_str = ";".join([f"{row['lon']},{row['lat']}" for _, row in df_sites.iterrows()])
    url = f"http://router.project-osrm.org/table/v1/driving/{coords_str}?annotations=distance,duration"
    
    status_text.text("🛣️ Calcul des itinéraires en cours...")
    progress_bar.progress(0.9) # On simule une fin proche

    try:
        response = requests.get(url, timeout=10).json()
        if response.get('code') == 'Ok':
            raw_dist = response['distances']
            raw_dur = response['durations']
            
            for i in range(n):
                for j in range(n):
                    # RÈGLE MÉTIER : Adresses identiques ou diagonale = 0
                    if i == j or df_sites.iloc[i]['adresse'] == df_sites.iloc[j]['adresse']:
                        dist_matrix[i][j] = 0.0
                        dur_matrix[i][j] = 0.0
                    else:
                        # Conversion : mètres -> km (arrondi 2), secondes -> min (arrondi 1)
                        dist_matrix[i][j] = round(raw_dist[i][j] / 1000, 2)
                        dur_matrix[i][j] = round(raw_dur[i][j] / 60, 1)
            return dist_matrix, dur_matrix
    except Exception as e:
        st.error(f"Erreur OSRM : {e}")
        return None, None

# ==========================================
# PARTIE 2 : INTERFACE STREAMLIT (UI)
# ==========================================

def run_matrix_tool():
    st.title("📍 Calculateur de Matrices Logistiques")
    st.markdown("---")

    # 1. Zone de saisie
    st.subheader("1. Saisie des sites et adresses")
    st.info("💡 **Instruction pour le collage :** Sélectionnez vos données dans Excel, faites Ctrl+C. Revenez ici, cliquez **UNE SEULE FOIS** sur la première case vide (elle doit avoir un contour bleu sans curseur clignotant) et faites Ctrl+V.")
    
    # On pré-remplit avec 5 lignes vides pour faciliter le "réceptacle" du copier-coller
    df_base = pd.DataFrame([{"site": "", "adresse": ""} for _ in range(5)])
    
    df_input = st.data_editor(
        df_base,
        num_rows="dynamic", 
        use_container_width=True,
        hide_index=True,
        column_config={
            "site": st.column_config.TextColumn("Nom du Site", width="medium"),
            "adresse": st.column_config.TextColumn("Adresse Complète", width="large")
        }
    )

    # 2. Bouton d'action
    if st.button("🚀 Générer les matrices", type="primary"):
        # 1. Conversion forcée en chaînes de caractères et renommage
        df_clean = df_input.copy()
        
        # On s'assure qu'on a au moins 2 colonnes
        if df_clean.shape[1] < 2:
            st.error("Le tableau doit avoir au moins 2 colonnes.")
            return

        # On renomme et on nettoie
        df_clean.columns = ['site', 'adresse'] + list(df_clean.columns[2:])
        
        for col in ['site', 'adresse']:
            df_clean[col] = df_clean[col].astype(str).replace(['None', 'nan', 'NaN'], '')
            df_clean[col] = df_clean[col].str.strip()

        # 2. Filtrage : on ne garde que ce qui n'est pas vide
        df_clean = df_clean[(df_clean['site'] != "") & (df_clean['adresse'] != "")]

        # --- DEBUG VISUEL (Temporaire) ---
        # st.write("Données détectées :", df_clean) 
        # ---------------------------------

        if len(df_clean) < 2:
            st.error(f"❌ Veuillez saisir au moins deux sites valides. (Détectés : {len(df_clean)})")
            st.info("💡 Astuce : Cliquez en dehors du tableau après avoir collé vos données pour valider la saisie.")
            return

        # Initialisation de la progression
        prog_bar = st.progress(0)
        status_txt = st.empty()

        # 3. Exécution : Géocodage
        coords_map = get_coordinates(df_clean, prog_bar, status_txt)
        df_clean['lat'] = df_clean['adresse'].map(lambda x: coords_map[x][0])
        df_clean['lon'] = df_clean['adresse'].map(lambda x: coords_map[x][1])

        # Vérification des erreurs de géocodage
        if df_clean['lat'].isnull().any():
            invalid_sites = df_clean[df_clean['lat'].isnull()]['site'].tolist()
            st.error(f"Erreur de géocodage pour les sites suivants : {invalid_sites}")
            return

        # 4. Exécution : Matrices
        dist_m, dur_m = calculate_osrm_matrices(df_clean, prog_bar, status_txt)

        if dist_m is not None:
            # Finalisation UI
            prog_bar.empty()
            status_txt.empty()
            st.success(f"✅ Matrices calculées pour {len(df_clean)} sites.")

            # Préparation des DataFrames pour l'affichage (index et colonnes = noms des sites)
            site_names = df_clean['site'].tolist()
            df_dist_final = pd.DataFrame(dist_m, index=site_names, columns=site_names)
            df_dur_final = pd.DataFrame(dur_m, index=site_names, columns=site_names)

            # 5. Affichage des résultats
            tab1, tab2 = st.tabs(["📏 Matrice des Distances (km)", "⏱ Matrice des Durées (min)"])
            with tab1:
                st.dataframe(df_dist_final, use_container_width=True)
            with tab2:
                st.dataframe(df_dur_final, use_container_width=True)

            # 6. Export Excel
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df_clean[['site', 'adresse', 'lat', 'lon']].to_excel(writer, sheet_name='Sites', index=False)
                df_dist_final.to_excel(writer, sheet_name='Distances_km')
                df_dur_final.to_excel(writer, sheet_name='Durees_min')
            
            st.download_button(
                label="📥 Télécharger le fichier Excel",
                data=output.getvalue(),
                file_name="matrices_transport_hopital.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
