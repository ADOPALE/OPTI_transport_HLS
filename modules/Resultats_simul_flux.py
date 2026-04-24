import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import streamlit as st


def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Affiche le planning GANTT avec une échelle temporelle de 04h00 à 23h00.
    """
    if not postes:
        st.warning("Aucune donnée de planification disponible.")
        return

    data = []
    # Filtrage des postes par type (en tant qu'objets)
    postes_filtres = [p for p in postes if getattr(p, 'vehicule_type', None) == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucun planning généré pour le type : {v_type_selectionne}")
        return

    for p in postes_filtres:
        if not hasattr(p, 'historique') or not p.historique:
            continue
            
        for ev in p.historique:
            data.append({
                "Poste": p.id_poste,
                "Début": ev["Minute_Debut"],
                "Fin": ev["Minute_Debut"] + 12, # On élargit un peu les blocs pour la visibilité
                "Activité": ev["Activite"],
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Détails": ev.get("Details", ""),
                "Heure": ev["Heure_Debut"]
            })

    if not data:
        st.info("L'historique est vide.")
        return

    df = pd.DataFrame(data)

    # Création du graphique
    fig = px.timeline(
        df, 
        x_start="Début", 
        x_end="Fin", 
        y="Poste", 
        color="Activité",
        hover_data=["Heure", "SJ_ID", "Détails"],
        title=f"Planning détaillé : {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION": "#1f77b4",
            "EN_TRAJET_VIDE": "#ff7f0e",
            "DISPONIBLE": "#2ca02c",
            "EN_PAUSE": "#d62728",
            "PRISE_POSTE": "#9467bd",
            "FIN_POSTE": "#7f7f7f"
        }
    )

    # --- CONFIGURATION DE L'ÉCHELLE (4h à 23h) ---
    # 4h = 240 min | 23h = 1380 min
    min_x = 240
    max_x = 1380

    fig.update_yaxes(autorange="reversed")
    
    fig.update_layout(
        xaxis_title="Heure de la journée (en minutes depuis 00:00)",
        # On force le type linéaire pour éviter que Plotly ne cherche des dates
        xaxis=dict(
            type='linear',
            range=[min_x, max_x],
            # On peut ajouter des étiquettes personnalisées pour lire des heures au lieu des minutes
            tickmode='array',
            tickvals=[240, 360, 480, 600, 720, 840, 960, 1080, 1200, 1320, 1380],
            ticktext=['04:00', '06:00', '08:00', '10:00', '12:00', '14:00', '16:00', '18:00', '20:00', '22:00', '23:00']
        ),
        showlegend=True,
        height=400 + (len(postes_filtres) * 25)
    )
    
    st.plotly_chart(fig, use_container_width=True)
