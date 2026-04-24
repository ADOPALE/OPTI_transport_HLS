import plotly.express as px
import plotly.figure_factory as ff

def generer_gantt_chauffeur_detaille(postes):
    """
    Génère un diagramme de Gantt ultra-détaillé pour l'ensemble des chauffeurs.
    Inclus : Missions, trajets à vide, origine/destination et volumes.
    """
    data_gantt = []

    for p in postes:
        chauffeur_id = p['id_chauffeur']
        camion_id = p.get('id_camion', 'Inconnu')
        v_type = p.get('v_type', 'N/A')
        
        # 1. Ajout de la Prise de Service (Temps fixe)
        h_debut = p['h_debut_service']
        data_gantt.append(dict(
            Chauffeur=f"{chauffeur_id} ({camion_id})",
            Start=h_debut,
            Finish=h_debut + 15, # On suppose 15min de prise de poste
            Type="Prise de service",
            Description="Préparation véhicule / Briefing",
            Volume=""
        ))

        last_h_fin = h_debut + 15
        last_pos = "HLS" # On part du dépôt par défaut

        for m in p['missions']:
            sj = m['sj']
            h_dep = m['h_dep']
            h_fin = m['h_fin']
            
            # --- A. Gestion de l'Attente ou Trajet à Vide avant mission ---
            if h_dep > last_h_fin:
                # On peut ici affiner si c'est du trajet à vide ou de l'attente
                data_gantt.append(dict(
                    Chauffeur=f"{chauffeur_id} ({camion_id})",
                    Start=last_h_fin,
                    Finish=h_dep,
                    Type="Attente / Trajet",
                    Description=f"Déplacement vers {sj['jobs'][0].origin}",
                    Volume=""
                ))

            # --- B. La Mission (Job) ---
            # Extraction des détails du Super Job
            details_flux = " | ".join([f"{j.origin}->{j.destination}" for j in sj['jobs']])
            nature = sj['type_combinaison']
            total_vol = sum([j.quantite for j in sj['jobs']])
            contenant = sj['jobs'][0].contenant

            data_gantt.append(dict(
                Chauffeur=f"{chauffeur_id} ({camion_id})",
                Start=h_dep,
                Finish=h_fin,
                Type="MISSION",
                Description=f"<b>Flux:</b> {details_flux}<br><b>Nature:</b> {nature}",
                Volume=f"{total_vol} {contenant}"
            ))
            
            last_h_fin = h_fin
            last_pos = sj['jobs'][-1].destination

        # 3. Fin de service (Retour dépôt)
        data_gantt.append(dict(
            Chauffeur=f"{chauffeur_id} ({camion_id})",
            Start=last_h_fin,
            Finish=last_h_fin + 15,
            Type="Fin de service",
            Description="Retour dépôt / Administratif",
            Volume=""
        ))

    # Conversion en DataFrame pour Plotly
    df_gantt = pd.DataFrame(data_gantt)
    
    # Transformation des minutes décimales en format Time pour l'axe X
    def min_to_time(minutes):
        base = datetime(2026, 1, 1, 0, 0)
        return base + timedelta(minutes=minutes)

    df_gantt['StartDT'] = df_gantt['Start'].apply(min_to_time)
    df_gantt['FinishDT'] = df_gantt['Finish'].apply(min_to_time)

    # Création du graphique
    fig = px.timeline(
        df_gantt, 
        x_start="StartDT", 
        x_end="FinishDT", 
        y="Chauffeur", 
        color="Type",
        hover_data={"Description": True, "Volume": True, "StartDT": False, "FinishDT": False},
        title=f"Planning Détaillé de la Flotte - {v_type}",
        color_discrete_map={
            "MISSION": "#1f77b4", 
            "Attente / Trajet": "#ff7f0e", 
            "Prise de service": "#2ca02c", 
            "Fin de service": "#d62728"
        }
    )

    fig.update_yaxes(autorange="reversed")
    fig.update_layout(xaxis_title="Heure de la journée", showlegend=True)
    
    return fig
