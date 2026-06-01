import pandas as pd
import plotly.express as px
import streamlit as st

import pandas as pd
import plotly.express as px
import streamlit as st

def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne, liste_globale_sj):
    """
    Affiche le planning GANTT avec un survol détaillé (Hover) affichant 
    le détail des jobs internes pour les missions.
    """
    if not postes:
        st.warning("⚠️ Aucun planning n'a été généré.")
        return

    data = []
    
    # 1. Filtrage des postes par type de véhicule
    postes_filtres = [p for p in postes if getattr(p, 'vehicule_type', None) == v_type_selectionne]

    if not postes_filtres:
        st.info(f"ℹ️ Aucune activité planifiée pour les véhicules : {v_type_selectionne}")
        return

    # 2. Construction du dataset
    for p in postes_filtres:
        if not hasattr(p, 'historique') or not p.historique:
            continue

        hist = sorted(p.historique, key=lambda x: x["Minute_Debut"])

        # Index des événements EN_MISSION par SJ_ID pour retrouver l'heure de prise en charge
        missions_par_sj = {
            ev["SJ_ID"]: ev["Minute_Debut"]
            for ev in hist if ev["Activite"] == "EN_MISSION" and ev.get("SJ_ID") != "N/A"
        }

        def min_to_heure(m):
            """Convertit des minutes depuis minuit en chaîne HHhMM."""
            return f"{int(m // 60):02d}h{int(m % 60):02d}"

        for i in range(len(hist)):
            ev = hist[i]
            debut = ev["Minute_Debut"]
            fin = hist[i + 1]["Minute_Debut"] if i < len(hist) - 1 else debut + 15
            duree = max(2, fin - debut)
            activite = ev["Activite"]

            # ── Contenu du hover selon le type d'activité ────────────────────
            hover_detail = ev.get("Details", "")

            if activite == "EN_MISSION" and ev.get("SJ_ID") != "N/A":
                # Chronologie complète du SuperJob en heures absolues
                sj_id = ev.get("SJ_ID")
                target_sj = next((s for s in liste_globale_sj if s.super_job_id == sj_id), None)
                if target_sj and hasattr(target_sj, "chronologie") and target_sj.chronologie:
                    t_ancre = debut  # heure absolue de démarrage de la mission
                    lignes = [f"<b>Mission {sj_id}</b> — {target_sj.type_logistique}"]
                    for etape in target_sj.chronologie:
                        h_deb = min_to_heure(t_ancre + etape["t_debut"])
                        h_fin = min_to_heure(t_ancre + etape["t_fin"])
                        qte = f" ({etape['quantite']} cont.)" if etape.get("quantite") else ""
                        lignes.append(f"{etape['etape']}. {etape['action']} @ {etape['site']}{qte} — {h_deb}→{h_fin}")
                    hover_detail = "<br>".join(lignes)
                else:
                    # Fallback si pas de chronologie : liste des jobs
                    if target_sj:
                        lignes = [f"<b>Mission {sj_id}</b>"]
                        for idx, j in enumerate(target_sj.liste_jobs):
                            lignes.append(f"Job {idx+1}: {getattr(j,'origin','?')} → {getattr(j,'destination','?')} ({getattr(j,'quantite',1)} cont.)")
                        hover_detail = "<br>".join(lignes)

            elif activite == "INTER_JOB":
                # Position (dernier déchargement) + horaires
                hover_detail = (
                    f"<b>Inter-job</b><br>"
                    f"Position : {p.position_actuelle if hasattr(p, 'position_actuelle') else '?'}<br>"
                    f"Début : {min_to_heure(debut)}<br>"
                    f"Fin   : {min_to_heure(fin)}"
                )

            elif activite in ("EN_TRAJET_VIDE", "RETOUR_DEPOT", "EN_RETOUR_DEPOT"):
                # Point de départ (enregistré dans position_depart) + point d'arrivée + horaires
                pt_depart = ev.get("position_depart", "?")
                # Point d'arrivée : pour EN_TRAJET_VIDE = départ du SJ, sinon = dépôt
                if activite == "EN_TRAJET_VIDE" and ev.get("SJ_ID") != "N/A":
                    target_sj = next((s for s in liste_globale_sj if s.super_job_id == ev.get("SJ_ID")), None)
                    pt_arrivee = target_sj.points_depart[0] if target_sj else p.stationnement_initial
                else:
                    pt_arrivee = p.stationnement_initial
                details_bruts = ev.get("Details", "")
                hover_detail = (
                    f"<b>{activite.replace('_', ' ').title()}</b><br>"
                    f"Départ  : {min_to_heure(debut)} — {pt_depart}<br>"
                    f"Arrivée : {min_to_heure(fin)} — {pt_arrivee}<br>"
                    f"{details_bruts}"
                )

            elif activite == "EN_PAUSE":
                hover_detail = (
                    f"<b>Pause</b><br>"
                    f"Début : {min_to_heure(debut)}<br>"
                    f"Fin   : {min_to_heure(fin)}"
                )

            data.append({
                "Poste": p.id_poste,
                "Début": debut,
                "Durée": duree,
                "Activité": activite,
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Heure": ev["Heure_Debut"],
                "Détails_Jobs": hover_detail,
            })

    df = pd.DataFrame(data)

    # 3. Création du graphique Plotly
    fig = px.bar(
        df,
        base="Début",
        x="Durée",
        y="Poste",
        color="Activité",
        orientation='h',
        hover_data={
            "Début": False, 
            "Durée": True, 
            "Heure": True, 
            "SJ_ID": True, 
            "Détails_Jobs": True # On affiche notre nouveau champ formaté
        },
        title=f"📅 Planning Opérationnel : {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION"    : "#1f77b4",   # Bleu — travail productif
            "EN_TRAJET_VIDE": "#ff7f0e",   # Orange — approche
            "INTER_JOB"     : "#aec7e8",   # Bleu clair — marge inter-job
            "DISPONIBLE"    : "#2ca02c",   # Vert — attente active
            "INTERMISSION"  : "#7f7f7f",   # Gris
            "PRISE_POSTE"   : "#9467bd",   # Violet — admin/prépa
            "PASSATION_FIN" : "#8c564b",   # Marron — fin de poste
            "RETOUR_DEPOT"  : "#e377c2",   # Rose — retour dépôt
            "EN_RETOUR_DEPOT": "#f7b6d2",  # Rose clair — retour dépôt interruptible
            "EN_PAUSE"      : "#d62728",   # Rouge — pause
            "VEHICULE_LIBERE": "#bcbd22",  # Jaune-vert — libéré
        }
    )

    # 4. Design
    fig.update_layout(
        xaxis=dict(
            title="Chronologie",
            range=[300, 1380],
            tickmode='array',
            tickvals=list(range(300, 1440, 60)),
            ticktext=[f"{h}h" for h in range(5, 24)],
            gridcolor='lightgray'
        ),
        yaxis=dict(autorange="reversed"),
        height=400 + (len(postes_filtres) * 30),
        hoverlabel=dict(bgcolor="black", font_size=12, font_family="Arial")
    )

    # Forcer l'affichage multi-ligne dans le hover
    fig.update_traces(hovertemplate="<b>%{y}</b><br>%{customdata[4]}<extra></extra>")

    st.plotly_chart(fig, use_container_width=True)


"""
def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):

    if not postes:
        st.warning("⚠️ Aucun planning n'a été généré.")
        return

    data = []
    
    # 1. Filtrage des postes par type de véhicule
    postes_filtres = [p for p in postes if getattr(p, 'vehicule_type', None) == v_type_selectionne]

    if not postes_filtres:
        st.info(f"ℹ️ Aucune activité planifiée pour les véhicules de type : {v_type_selectionne}")
        return

    # 2. Construction du dataset pour Plotly
    for p in postes_filtres:
        if not hasattr(p, 'historique') or not p.historique:
            continue
            
        # Tri de l'historique par chronologie
        hist = sorted(p.historique, key=lambda x: x["Minute_Debut"])
        
        for i in range(len(hist)):
            ev = hist[i]
            debut = ev["Minute_Debut"]
            
            # Détermination de la fin du bloc
            # Si c'est le dernier événement, on lui donne une durée arbitraire (ex: fin de service)
            if i < len(hist) - 1:
                fin = hist[i+1]["Minute_Debut"]
            else:
                fin = debut + 15  # 15 min par défaut pour marquer la fin
            
            duree = max(2, fin - debut)

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
        st.error("❌ Les données d'historique sont corrompues ou vides.")
        return

    df = pd.DataFrame(data)

    # 3. Création du graphique Plotly
    fig = px.bar(
        df,
        base="Début",       # Point de départ sur l'axe X (minutes depuis minuit)
        x="Durée",         # Largeur de la barre
        y="Poste",
        color="Activité",
        orientation='h',
        hover_data={
            "Début": False, 
            "Durée": True, 
            "Heure": True, 
            "SJ_ID": True, 
            "Détails": True
        },
        title=f"📅 Planning Opérationnel : {v_type_selectionne}",
        # Couleurs harmonisées avec les états du moteur
        color_discrete_map={
            "EN_MISSION": "#1f77b4",       # Bleu (Travail)
            "EN_TRAJET_VIDE": "#ff7f0e",   # Orange (Déplacement/Approche)
            "DISPONIBLE": "#2ca02c",       # Vert (Attente/Flexibilité)
            "EN_PAUSE": "#d62728",         # Rouge (Repos)
            "PRISE_POSTE": "#9467bd",      # Violet (Admin/Prépa)
            "PASSATION_POSTE": "#8c564b",  # Marron (Relève)
            "TRANSITION": "#7f7f7f"        # Gris (Changement d'état)
        }
    )

    # 4. Optimisation des axes et du design
    fig.update_layout(
        xaxis=dict(
            title="Chronologie de la journée",
            range=[300, 1380], # Fenêtre de 05h00 à 23h00
            tickmode='array',
            tickvals=[300, 360, 420, 480, 540, 600, 660, 720, 780, 840, 900, 960, 1020, 1080, 1140, 1200, 1260, 1320, 1380],
            ticktext=['05h', '06h', '07h', '08h', '09h', '10h', '11h', '12h', '13h', '14h', '15h', '16h', '17h', '18h', '19h', '20h', '21h', '22h', '23h'],
            gridcolor='lightgray'
        ),
        yaxis=dict(
            title="Identifiant Véhicule / Chauffeur", 
            autorange="reversed" # Le chauffeur 1 reste en haut
        ),
        legend_title_text="Légende Activités",
        height=400 + (len(postes_filtres) * 25), # Hauteur dynamique selon le nombre de camions
        barmode='stack', # Empilement horizontal
        hoverlabel=dict(bgcolor="black", font_size=12)
    )

    # Ajout d'une ligne verticale pour l'heure actuelle (optionnel mais utile)
    maintenant = pd.Timestamp.now()
    min_actuelle = maintenant.hour * 60 + maintenant.minute
    if 300 <= min_actuelle <= 1380:
        fig.add_vline(x=min_actuelle, line_width=2, line_dash="dash", line_color="red")

    st.plotly_chart(fig, use_container_width=True)

    """
