import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

def afficher_tableau_bord_global(resultats_sim):
    """
    Affiche les KPI de haut niveau (Besoin max, moyenne, distance totale).
    Exploite le dictionnaire 'kpis' généré par le moteur.
    """
    st.header("1. Dimensionnement et Indicateurs Globaux")
    
    df_t = resultats_sim["tournees"]
    kpis = resultats_sim["kpis"]
    
    # --- Ligne de KPIs ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Besoin Max Chauffeurs", f"{kpis['nb_chauffeurs_max_jour']} ETP")
    c2.metric("Total Tournées", f"{kpis['nb_tournees']}")
    c3.metric("Remplissage Moyen", f"{kpis['remplissage_moyen']:.1f} %")
    c4.metric("Distance Totale", f"{int(kpis['distance_totale'])} km")

    # --- Graphique d'activité ---
    st.subheader("Nombre de tournées par jour")
    # Réorganisation pour assurer l'ordre chronologique des jours
    ordre_jours = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    df_jour = df_t.groupby("Jour").size().reindex(ordre_jours).fillna(0).reset_index()
    df_jour.columns = ["Jour", "Nombre de tournées"]
    
    fig_bar = px.bar(df_jour, x="Jour", y="Nombre de tournées", color_discrete_sequence=['#2E7D32'])
    st.plotly_chart(fig_bar, use_container_width=True)


def afficher_analyse_operationnelle(resultats_sim, df_vehicules, df_contenants):
    """
    Interface interactive pour explorer le détail des tournées.
    Inclut la Timeline (Gantt) et le plan de chargement (Bin Packing).
    """
    st.header("2. Analyse Opérationnelle Détaillée")
    
    jours_nom = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
    chauffeurs_obj = resultats_sim["obj_chauffeurs"]
    
    # --- FILTRE 1 : LE JOUR ---
    jour_selected = st.selectbox("📅 Choisir un jour", jours_nom, key="sel_jour_op")
    
    # Filtrage des chauffeurs ayant travaillé ce jour-là
    chauffeurs_du_jour = [c for c in chauffeurs_obj if c.id.split('_')[1] == jour_selected]
    
    if not chauffeurs_du_jour:
        st.warning(f"Aucune activité planifiée pour le {jour_selected}.")
        return

    # --- FILTRE 2 : LE CHAUFFEUR ---
    # Création d'un label lisible pour l'utilisateur
    mapping_ch = {f"{c.id} ({c.tournees[0].vehicule_type})": c for c in chauffeurs_du_jour}
    ch_label_sel = st.selectbox("🚚 Choisir un chauffeur / véhicule", list(mapping_ch.keys()), key="sel_chauf_op")
    c_selected = mapping_ch[ch_label_sel]

    # --- AFFICHAGE DE LA TIMELINE (GANTT) ---
    st.subheader(f"Chronologie de la journée : {ch_label_sel}")
    
    df_gantt = []
    temps_cumule = 480  # Hypothèse de départ à 08:00 (480 min)
    
    for i, t in enumerate(c_selected.tournees):
        start_time = pd.Timestamp("2026-01-01") + pd.Timedelta(minutes=temps_cumule)
        end_time = start_time + pd.Timedelta(minutes=t.duree_totale)
        
        df_gantt.append(dict(
            Task="Tournées",
            Start=start_time,
            Finish=end_time,
            Tournee=f"Tournée {t.id}",
            Details=f"Charge: {t.remplissage_actuel:.1f} / {t.capacite_max:.1f} contenants"
        ))
        # Marge de battement entre deux tournées (manutention, pauses...)
        temps_cumule += t.duree_totale + 15 

    fig_timeline = px.timeline(
        df_gantt, 
        x_start="Start", 
        x_end="Finish", 
        y="Task", 
        color="Tournee",
        hover_data=["Details"],
        text="Tournee",
        color_discrete_sequence=px.colors.qualitative.Pastel
    )
    fig_timeline.update_layout(showlegend=False, height=200, margin=dict(t=10, b=10))
    fig_timeline.update_xaxes(tickformat="%H:%M")
    st.plotly_chart(fig_timeline, use_container_width=True)

    # --- MÉTRIQUES DU CHAUFFEUR ---
    m1, m2, m3 = st.columns(3)
    m1.metric("Amplitude Travail", f"{int(c_selected.temps_travail_cumule)} min")
    m2.metric("Nombre de rotations", len(c_selected.tournees))
    rempl_moyen = np.mean([t.remplissage_actuel/t.capacite_max for t in c_selected.tournees]) * 100
    m3.metric("Remplissage Moyen", f"{rempl_moyen:.1f} %")

    st.divider()

    # --- FILTRE 3 : LA TOURNÉE (SÉLECTION POUR VISUEL) ---
    mapping_t = {f"Tournée {t.id} (Hub: {t.hub_depart})": t for t in c_selected.tournees}
    t_label_sel = st.selectbox("📦 Sélectionner une tournée pour voir le chargement", list(mapping_t.keys()), key="sel_rot_op")
    t_selected = mapping_t[t_label_sel]

    # --- AFFICHAGE DU BIN PACKING (Vue Camion) ---
    st.subheader("📦 Plan de chargement (Vue de dessus)")
    
    # Import local du moteur pour la fonction de visuel
    from modules.simul_flux import generer_visuel_bin_packing
    
    fig_bin = generer_visuel_bin_packing(t_selected, df_vehicules, df_contenants)
    
    col_text, col_plot = st.columns([1, 2])
    with col_text:
        st.markdown(f"""
        **Détails de l'emport :**
        - **Origine :** {t_selected.hub_depart}
        - **Itinéraire :** {' ➔ '.join(t_selected.itineraire)}
        - **Contenants :** {int(t_selected.remplissage_actuel)} unités
        
        ---
        **Guide de lecture :**
        - Chaque rectangle représente un chariot/contenant.
        - La couleur correspond à la **destination**.
        - **Passez la souris** sur un rectangle pour voir :
            1. Le type de contenant.
            2. La fonction support (quoi ?).
            3. Le site de livraison (où ?).
        """)
        
    with col_plot:
        st.plotly_chart(fig_bin, use_container_width=True)

    # --- TABLEAU DES JOBS ---
    with st.expander("Voir la liste détaillée des flux de cette tournée"):
        df_jobs_t = pd.DataFrame([{
            "Job ID": j.id,
            "Origine": j.origine,
            "Destination": j.destination,
            "Fonction Support": j.fonction_support,
            "Type Contenant": j.type_contenant,
            "Quantité": j.quantite
        } for j in t_selected.jobs])
        st.table(df_jobs_t)


def generer_graphique_gantt(tournees_df):
    """
    Fonction utilitaire pour créer une timeline globale si nécessaire.
    """
    fig = px.timeline(
        tournees_df, 
        x_start="Start", 
        x_end="Finish", 
        y="ID Tournée", 
        color="Véhicule",
        title="Vue d'ensemble des rotations"
    )
    fig.update_yaxes(autorange="reversed")
    return fig
