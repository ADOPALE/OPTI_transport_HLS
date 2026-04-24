import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import streamlit as st


def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Affiche le planning GANTT pour les objets PosteChauffeur.
    Compatible avec la structure SuperJob (poids_total).
    """
    if not postes:
        st.warning("Aucun poste à afficher (liste vide).")
        return

    data = []
    
    # CORRECTION : On accède à l'attribut .vehicule_type (objet) et non .get() (dict)
    postes_filtres = [p for p in postes if p.vehicule_type == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucune activité enregistrée pour le type de véhicule : {v_type_selectionne}")
        return

    for p in postes_filtres:
        if not p.historique:
            continue
            
        for ev in p.historique:
            # On définit une durée visuelle pour chaque bloc dans le Gantt
            # L'historique enregistre le DEBUT de chaque état.
            # Pour l'affichage, on estime la fin à Minute_Debut + 5 (le pas) 
            # ou on laisse Plotly gérer la continuité.
            
            data.append({
                "Poste": p.id_poste,
                "Début": ev["Minute_Debut"],
                "Fin": ev["Minute_Debut"] + 5, # Pas par défaut pour la visualisation
                "Activité": ev["Activite"],
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Origine": ev.get("Origine", ""),
                "Destination": ev.get("Destination", ""),
                "Détails": ev.get("Details", ""),
                "Heure_Debut": ev["Heure_Debut"]
            })

    if not data:
        st.info("L'historique des postes sélectionnés est vide.")
        return

    df = pd.DataFrame(data)

    # Création du graphique Gantt via Plotly
    fig = px.timeline(
        df, 
        x_start="Début", 
        x_end="Fin", 
        y="Poste", 
        color="Activité",
        hover_data=["Heure_Debut", "SJ_ID", "Origine", "Destination", "Détails"],
        title=f"Planning Chronologique - {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION": "#1f77b4",      # Bleu (SuperJob en cours)
            "EN_TRAJET_VIDE": "#ff7f0e",  # Orange (Approche ou retour dépôt)
            "DISPONIBLE": "#2ca02c",      # Vert
            "EN_PAUSE": "#d62728",        # Rouge
            "PRISE_POSTE": "#9467bd",     # Violet
            "FIN_POSTE": "#7f7f7f"        # Gris
        }
    )

    # Inverser l'axe Y pour avoir le premier camion en haut
    fig.update_yaxes(autorange="reversed")
    
    # Configuration de l'axe X pour qu'il soit lisible (minutes de la journée)
    fig.update_layout(
        xaxis_title="Minutes écoulées depuis le début de journée",
        xaxis=dict(type='linear'),
        showlegend=True,
        height=400 + (len(postes_filtres) * 20) # Taille dynamique
    )
    
    st.plotly_chart(fig, use_container_width=True)
