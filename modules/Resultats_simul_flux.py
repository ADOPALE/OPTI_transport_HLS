import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import streamlit as st

def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Génère un diagramme de Gantt détaillé pour les chauffeurs d'un type de véhicule.
    Affiche tous les contenants et itinéraires pour les jobs combinés.
    """
    if not postes:
        st.warning(f"Aucune donnée à afficher pour le type {v_type_selectionne}")
        return

    data_gantt = []
    
    # Filtrer les postes pour le type sélectionné
    postes_filtres = [p for p in postes if p.get('v_type') == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucun planning généré pour {v_type_selectionne}")
        return

    for p in postes_filtres:
        ch_id = p['id_chauffeur']
        camion_id = p.get('id_camion', 'N/A')
        
        # 1. Prise de service
        h_prise = p['h_debut_service']
        data_gantt.append(dict(
            Chauffeur=ch_id,
            Start=h_prise,
            Finish=h_prise + 15,
            Type="🔧 Prise de service",
            Description=f"Camion: {camion_id}<br>Préparation et briefing",
            Volume=""
        ))

        curr_h = h_prise + 15

        # 2. Missions et Trajets/Attentes
        for m in p['missions']:
            sj = m['sj']
            h_dep = m['h_dep']
            h_fin = m['h_fin']
            
            # --- Gestion de l'intervalle avant mission ---
            if h_dep > curr_h:
                data_gantt.append(dict(
                    Chauffeur=ch_id,
                    Start=curr_h,
                    Finish=h_dep,
                    Type="⏳ Attente/Trajet Vide",
                    Description=f"Liaison vers {sj['jobs'][0].origin}",
                    Volume=""
                ))

            # --- LOGIQUE D'AGRÉGATION MULTI-JOBS ---
            dict_volumes = {}
            itineraires = []
            
            for j in sj['jobs']:
                # On ajoute l'itinéraire du job s'il n'est pas déjà dans la liste
                iti = f"{j.origin} → {j.destination}"
                if iti not in itineraires:
                    itineraires.append(iti)
                
                # On cumule les quantités par type de contenant
                c_type = j.contenant
                dict_volumes[c_type] = dict_volumes.get(c_type, 0) + j.quantite

            # Formatage du texte pour le volume (ex: "12 ROLLS, 4 PALETTES")
            volume_str = ", ".join([f"{int(q)} {str(c).upper()}" for c, q in dict_volumes.items()])
            details_iti = " | ".join(itineraires)
            nature = sj.get('type_combinaison', 'DIRECT')

            data_gantt.append(dict(
                Chauffeur=ch_id,
                Start=h_dep,
                Finish=h_fin,
                Type="🚛 MISSION",
                Description=f"<b>Itinéraires :</b> {details_iti}<br><b>Nature :</b> {nature}",
                Volume=volume_str
            ))
            
            curr_h = h_fin

        # 3. Fin de service
        data_gantt.append(dict(
            Chauffeur=ch_id,
            Start=curr_h,
            Finish=curr_h + 15,
            Type="🏁 Fin de service",
            Description="Retour dépôt et administratif",
            Volume=""
        ))

    # Conversion en DataFrame
    df = pd.DataFrame(data_gantt)

    # Conversion des minutes en format Datetime pour Plotly
    def to_dt(minutes):
        return datetime(2026, 1, 1) + timedelta(minutes=minutes)

    df['StartDT'] = df['Start'].apply(to_dt)
    df['FinishDT'] = df['Finish'].apply(to_dt)

    # Création du graphique Plotly
    fig = px.timeline(
        df, 
        x_start="StartDT", 
        x_end="FinishDT", 
        y="Chauffeur", 
        color="Type",
        # On ajoute 'Volume' et 'Description' dans les données de survol
        hover_data={
            "Type": True,
            "Description": True, 
            "Volume": True, 
            "StartDT": False, 
            "FinishDT": False,
            "Chauffeur": False
        },
        color_discrete_map={
            "🚛 MISSION": "#00CC96",          # Vert
            "⏳ Attente/Trajet Vide": "#EF553B", # Rouge/Orange
            "🔧 Prise de service": "#636EFA",    # Bleu
            "🏁 Fin de service": "#AB63FA"       # Violet
        }
    )

    # Inverser l'axe Y pour avoir le Chauffeur 1 en haut
    fig.update_yaxes(autorange="reversed")
    
    # Mise en forme des axes et de la légende
    fig.update_layout(
        title=f"Planning opérationnel détaillé - {v_type_selectionne}",
        xaxis_tickformat="%H:%M",
        height=400 + (len(postes_filtres) * 40), # Hauteur dynamique selon le nombre de chauffeurs
        xaxis_title="Heure de la journée",
        yaxis_title=None,
        legend_title="Activité",
        hoverlabel=dict(bgcolor="white", font_size=12)
    )

    st.plotly_chart(fig, use_container_width=True)
