
import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go


def afficher_analyse_operationnelle(planning, df_vehicules, df_contenants):
    """
    Interface interactive pour explorer le détail des tournées.
    """
    st.header("2. Analyse Opérationnelle Détaillée")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    
    # --- FILTRE 1 : LE JOUR ---
    jour_selected = st.selectbox("📅 Choisir un jour", jours_nom, key="sel_jour_op")
    key_jour = f"Quantité {jour_selected}"
    
    data_jour = planning.get(key_jour, {"chauffeurs": []})
    
    if not data_jour['chauffeurs']:
        st.warning(f"Aucune activité planifiée pour le {jour_selected}.")
        return

    # --- FILTRE 2 : LE CHAUFFEUR ---
    labels_chauffeurs = [f"Chauffeur {i+1} ({c['type_vehicule']})" for i, c in enumerate(data_jour['chauffeurs'])]
    chauffeur_sel = st.selectbox("🚚 Choisir un chauffeur / véhicule", labels_chauffeurs, key="sel_chauf_op")
    idx_c = labels_chauffeurs.index(chauffeur_sel)
    c_data = data_jour['chauffeurs'][idx_c]

    # --- AFFICHAGE DE LA TIMELINE (GANTT) ---
    st.subheader(f"Chronologie de la journée : {chauffeur_sel}")
    
    # Préparation des données pour Plotly Gantt
    df_gantt = []
    for i, t in enumerate(c_data['tournees']):
        # Conversion des minutes en format Time pour l'affichage
        start_time = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['debut'])
        end_time = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['fin'])
        
        df_gantt.append(dict(
            Task=f"Tournée {i+1}",
            Start=start_time,
            Finish=end_time,
            Resource=t['label'],
            Description=f"Contenu: {t['contenant_qte']} {t['contenant_type']}"
        ))

    fig_timeline = px.timeline(
        df_gantt, 
        x_start="Start", 
        x_end="Finish", 
        y="Task", 
        color="Resource",
        hover_data=["Description"]
    )
    fig_timeline.update_yaxes(autorange="reversed")
    fig_timeline.update_layout(showlegend=False, height=300)
    st.plotly_chart(fig_timeline, use_container_width=True)

    # Métriques rapides du chauffeur
    m1, m2, m3 = st.columns(3)
    m1.metric("Amplitude Travail", f"{int(c_data['t_total'])} min")
    m2.metric("Temps de Roulage", f"{int(c_data['t_roulage'])} min")
    m3.metric("Temps Manutention", f"{int(c_data['t_manut'])} min")

    st.divider()

    # --- FILTRE 3 : LA TOURNÉE (POUR LE BIN PACKING) ---
    labels_rot = [f"Tournée {i+1} : {r['label']}" for i, r in enumerate(c_data['tournees'])]
    rot_sel = st.selectbox("📦 Sélectionner une tournée pour voir le chargement", labels_rot, key="sel_rot_op")
    idx_r = labels_rot.index(rot_sel)
    r_data = c_data['tournees'][idx_r]

    # --- AFFICHAGE DU VISUEL BIN PACKING ---
    st.subheader("📦 Plan de chargement (Visuel Camion)")
    
    # Import local pour éviter les imports circulaires
    from modules.simul_flux import generer_visuel_bin_packing
    
    fig_bin = generer_visuel_bin_packing(
        r_data['contenant_type'], 
        r_data['contenant_qte'], 
        c_data['type_vehicule'], 
        df_vehicules, 
        df_contenants
    )
    
    # Affichage du dictionnaire de données de la tournée pour contrôle
    col_text, col_plot = st.columns([1, 2])
    with col_text:
        st.info(f"""
        **Détails de l'emport :**
        * **Origine :** {r_data['label'].split('->')[0]}
        * **Destination :** {r_data['label'].split('->')[1]}
        * **Contenant :** {r_data['contenant_type']}
        * **Quantité :** {r_data['contenant_qte']} unités
        """)
        
    with col_plot:
        # Rendu du graphique Plotly généré dans simul_flux.py
        st.plotly_chart(fig_bin, use_container_width=True)



def afficher_tableau_bord_global(planning):
    """Affiche les KPI de haut niveau (Besoin max, moyenne, tableau hebdo)."""
    st.header("1. Dimensionnement RH Global")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    nb_postes = [len(planning[f"Quantité {j}"]['chauffeurs']) for j in jours_nom]
    
    col1, col2 = st.columns(2)
    col1.metric("Besoin Max (ETP)", f"{max(nb_postes)} chauffeurs")
    col2.metric("Moyenne Semaine", f"{np.mean(nb_postes):.1f}")

    # Tableau récapitulatif
    df_recap = pd.DataFrame({"Jour": jours_nom, "Nombre de postes (7h30)": nb_postes})
    st.table(df_recap)



def generer_graphique_gantt(tournees):
    """Crée une timeline propre avec Plotly."""
    df_list = []
    for i, t in enumerate(tournees):
        # On crée des dates fictives pour que Plotly Gantt fonctionne (base 01/01/2024)
        start = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['debut'])
        end = pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=t['fin'])
        
        df_list.append(dict(
            Task=f"Tournée {i+1}",
            Start=start,
            Finish=end,
            Resource=t['label']
        ))
    
    fig = px.timeline(df_list, x_start="Start", x_end="Finish", y="Task", color="Resource",
                      title="Chronologie de la journée (HH:MM)")
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(showlegend=False)
    return fig
