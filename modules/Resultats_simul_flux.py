import pandas as pd
import plotly.express as px
import streamlit as st

def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Affiche le planning GANTT en utilisant px.bar pour garantir l'affichage des minutes.
    """
    if not postes:
        st.warning("Aucun poste reçu par la fonction.")
        return

    data = []
    # Filtrage des objets PosteChauffeur
    postes_filtres = [p for p in postes if getattr(p, 'vehicule_type', None) == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucune activité trouvée pour le type : {v_type_selectionne}")
        return

    for p in postes_filtres:
        if not hasattr(p, 'historique') or not p.historique:
            continue
            
        hist = sorted(p.historique, key=lambda x: x["Minute_Debut"])
        
        for i in range(len(hist)):
            ev = hist[i]
            debut = ev["Minute_Debut"]
            # Calcul de la durée du bloc
            if i < len(hist) - 1:
                fin = hist[i+1]["Minute_Debut"]
            else:
                fin = debut + 20 # 20 min par défaut pour le dernier état
            
            duree = max(2, fin - debut) # Minimum 2 min pour que ce soit visible

            data.append({
                "Poste": p.id_poste,
                "Début": debut,
                "Durée": duree,
                "Activité": ev["Activite"],
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Heure": ev["Heure_Debut"],
                "Détails": ev.get("Details", "")
            })

    if not data:
        st.error("L'historique des postes est vide.")
        return

    df = pd.DataFrame(data)

    # On utilise px.bar au lieu de px.timeline pour éviter les conflits de format date
    fig = px.bar(
        df,
        base="Début",       # Le point de départ sur l'axe X
        x="Durée",         # La longueur de la barre
        y="Poste",
        color="Activité",
        orientation='h',
        hover_data=["Heure", "SJ_ID", "Détails"],
        title=f"Planning Chauffeurs : {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION": "#1f77b4",
            "EN_TRAJET_VIDE": "#ff7f0e",
            "DISPONIBLE": "#2ca02c",
            "EN_PAUSE": "#d62728",
            "PRISE_POSTE": "#9467bd",
            "FIN_POSTE": "#7f7f7f"
        }
    )

    # Configuration de l'échelle de 4h (240) à 23h (1380)
    fig.update_layout(
        xaxis=dict(
            title="Heure de la journée",
            range=[240, 1380],
            tickmode='array',
            tickvals=[240, 360, 480, 600, 720, 840, 960, 1080, 1200, 1320, 1380],
            ticktext=['04h', '06h', '08h', '10h', '12h', '14h', '16h', '18h', '20h', '22h', '23h']
        ),
        yaxis=dict(title="Camions", autorange="reversed"),
        height=450 + (len(postes_filtres) * 20),
        barmode='stack' # Important pour empiler les activités sur la même ligne
    )
    
    st.plotly_chart(fig, use_container_width=True)
