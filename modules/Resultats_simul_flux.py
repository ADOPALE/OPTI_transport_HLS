import pandas as pd
import plotly.express as px
import streamlit as st

def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Affiche le planning GANTT avec une échelle forcée en minutes de 4h à 23h.
    """
    if not postes:
        st.warning("Aucune donnée de planification disponible (liste des postes vide).")
        return

    data = []
    # Filtrage rigoureux des objets PosteChauffeur
    postes_filtres = [p for p in postes if getattr(p, 'vehicule_type', None) == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucune activité pour : {v_type_selectionne}")
        return

    for p in postes_filtres:
        if not hasattr(p, 'historique') or not p.historique:
            continue
            
        # On trie l'historique par minute pour calculer les durées réelles
        hist_trie = sorted(p.historique, key=lambda x: x["Minute_Debut"])
        
        for i in range(len(hist_trie)):
            ev = hist_trie[i]
            debut = ev["Minute_Debut"]
            
            # Calcul de la fin : soit le début de l'événement suivant, 
            # soit le début + 15 min si c'est le dernier (pour qu'il soit visible)
            if i < len(hist_trie) - 1:
                fin = hist_trie[i+1]["Minute_Debut"]
            else:
                fin = debut + 15 
            
            # Sécurité : si debut == fin (pas de temps nul), on donne 5min pour voir le bloc
            if fin <= debut:
                fin = debut + 5

            data.append({
                "Poste": p.id_poste,
                "Début": debut,
                "Fin": fin,
                "Activité": ev["Activite"],
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Détails": ev.get("Details", ""),
                "Heure": ev["Heure_Debut"]
            })

    if not data:
        st.info("L'historique est vide pour ces véhicules.")
        return

    df = pd.DataFrame(data)

    # Création du graphique en mode 'linear' pour l'axe X
    fig = px.timeline(
        df, 
        x_start="Début", 
        x_end="Fin", 
        y="Poste", 
        color="Activité",
        hover_data=["Heure", "SJ_ID", "Détails"],
        title=f"Planning Chauffeurs : {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION": "#1f77b4",     # Bleu
            "EN_TRAJET_VIDE": "#ff7f0e", # Orange
            "DISPONIBLE": "#2ca02c",     # Vert
            "EN_PAUSE": "#d62728",       # Rouge
            "PRISE_POSTE": "#9467bd",    # Violet
            "FIN_POSTE": "#7f7f7f"       # Gris
        }
    )

    # CONFIGURATION DE L'AXE X (4h - 23h)
    # 4h = 240, 23h = 1380
    min_x, max_x = 240, 1380

    fig.update_yaxes(autorange="reversed")
    
    fig.update_layout(
        xaxis_title="Heure de la journée",
        # CRITIQUE : forcer le mode linéaire pour que Début/Fin (240, 250...) fonctionnent
        xaxis=dict(
            type='linear',
            range=[min_x, max_x],
            tickmode='array',
            tickvals=[240, 360, 480, 600, 720, 840, 960, 1080, 1200, 1320, 1380],
            ticktext=['04h', '06h', '08h', '10h', '12h', '14h', '16h', '18h', '20h', '22h', '23h']
        ),
        height=450 + (len(postes_filtres) * 20),
        margin=dict(l=20, r=20, t=40, b=20)
    )
    
    st.plotly_chart(fig, use_container_width=True)
