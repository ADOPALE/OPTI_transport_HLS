import streamlit as st
import folium
from streamlit_folium import st_folium
import numpy as np

def show_sites_on_map():
    """
    Affiche la carte interactive des flux de biologie.
    Filtrage, calcul des rayons proportionnels et affichage des plages horaires.
    """
    # 1. Récupération sécurisée des données
    if "data" not in st.session_state or "param_sites" not in st.session_state["data"]:
        st.error("Données 'param_sites' introuvables dans st.session_state['data'].")
        return

    df = st.session_state["data"]["param_sites"]

    # 2. Filtrage strict sur la biologie (Colonnes demandées)
    # On utilise str.contains avec na=False pour éviter les erreurs sur cellules vides
    mask = (
        (df["Fonction Support associée"].astype(str).str.contains("biologie", case=False, na=False)) &
        (df["Nature du flux Fréquences"].astype(str).str.contains("biologie", case=False, na=False))
    )
    df_bio = df[mask].copy()

    # 3. Nettoyage des coordonnées manquantes
    df_bio = df_bio.dropna(subset=['latitude', 'longitude'])

    if df_bio.empty:
        st.warning("Aucun site correspondant aux critères 'biologie' n'a été trouvé.")
        return

    # 4. Initialisation de la carte (centrée sur les points filtrés)
    m = folium.Map(
        location=[df_bio['latitude'].mean(), df_bio['longitude'].mean()],
        zoom_start=11,
        control_scale=True
    )

    # 5. Ajout des éléments visuels
    for _, row in df_bio.iterrows():
        freq = float(row.get('frequence', 0))
        plage = str(row.get('plage_horaire', 'N/A'))
        nom = str(row.get('libelle', 'Site'))

        # Calcul du rayon : l'aire du cercle est proportionnelle à la fréquence
        # On multiplie par un facteur (ex: 5) pour la visibilité
        radius_size = np.sqrt(freq) * 5 if freq > 0 else 2

        # Cercle proportionnel
        folium.CircleMarker(
            location=[row['latitude'], row['longitude']],
            radius=radius_size,
            color="#003399",  # Bleu profond
            fill=True,
            fill_color="#3366ff",
            fill_opacity=0.6,
            popup=f"<b>{nom}</b><br>Fréquence : {freq}<br>Plage : {plage}"
        ).add_to(m)

        # Étiquette de la plage horaire à côté du site
        folium.Marker(
            location=[row['latitude'], row['longitude']],
            icon=folium.DivIcon(
                icon_size=(150, 36),
                icon_anchor=(-10, 5),
                html=f"""<div style="font-family: sans-serif; font-size: 9pt; color: #003399; font-weight: bold;">
                         <span style="background-color: rgba(255,255,255,0.7); padding: 2px;">{plage}</span>
                         </div>"""
            )
        ).add_to(m)

    # 6. Rendu Streamlit
    st_folium(m, width=800, height=500, returned_objects=[])
