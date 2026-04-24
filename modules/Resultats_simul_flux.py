import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta
import streamlit as st

def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Génère un diagramme de Gantt détaillé pour les chauffeurs d'un type de véhicule.
    """
    if not postes:
        st.warning(f"Aucune donnée à afficher pour le type {v_type_selectionne}")
        return

    data_gantt = []
    
    # Filtrer les postes pour le type sélectionné
    postes_filtres = [p for p in postes if p.get('v_type') == v_type_selectionne]

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
        curr_pos = "Dépôt (HLS)"

        # 2. Missions et Trajets/Attentes
        for i, m in enumerate(p['missions']):
            sj = m['sj']
            h_dep = m['h_dep']
            h_fin = m['h_fin']
            
            # Si intervalle > 0, on identifie si c'est de l'attente ou du trajet
            if h_dep > curr_h:
                data_gantt.append(dict(
                    Chauffeur=ch_id,
                    Start=curr_h,
                    Finish=h_dep,
                    Type="⏳ Attente/Trajet Vide",
                    Description=f"Liaison vers {sj['jobs'][0].origin}",
                    Volume=""
                ))

            # Détails de la mission
            details_flux = " > ".join([f"{j.origin}→{j.destination}" for j in sj['jobs']])
            total_qty = sum(j.quantite for j in sj['jobs'])
            contenant = sj['jobs'][0].contenant
            nature = sj.get('type_combinaison', 'DIRECT')

            data_gantt.append(dict(
                Chauffeur=ch_id,
                Start=h_dep,
                Finish=h_fin,
                Type="🚛 MISSION",
                Description=f"<b>Itinéraire:</b> {details_flux}<br><b>Nature:</b> {nature}",
                Volume=f"{total_qty} {contenant}"
            ))
            
            curr_h = h_fin
            curr_pos = sj['jobs'][-1].destination

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

    # Conversion des minutes en format Time pour Plotly
    def to_dt(minutes):
        return datetime(2026, 1, 1) + timedelta(minutes=minutes)

    df['StartDT'] = df['Start'].apply(to_dt)
    df['FinishDT'] = df['Finish'].apply(to_dt)

    # Création du Plotly Timeline
    fig = px.timeline(
        df, 
        x_start="StartDT", 
        x_end="FinishDT", 
        y="Chauffeur", 
        color="Type",
        hover_data={"Description": True, "Volume": True, "StartDT": False, "FinishDT": False},
        color_discrete_map={
            "🚛 MISSION": "#00CC96",
            "⏳ Attente/Trajet Vide": "#EF553B",
            "🔧 Prise de service": "#636EFA",
            "🏁 Fin de service": "#AB63FA"
        }
    )

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(
        title=f"Planning détaillé des chauffeurs - {v_type_selectionne}",
        xaxis_tickformat="%H:%M",
        height=400 + (len(postes_filtres) * 30), # Hauteur dynamique
        xaxis_title="Heures",
        legend_title="Activité"
    )

    st.plotly_chart(fig, use_container_width=True)
