import pandas as pd
import math
from datetime import time, datetime
import plotly.express as px
import streamlit as st

# =================================================================
# 1. UTILITAIRES
# =================================================================

def to_decimal_minutes(t):
    """Convertit Time, Timedelta ou Float en minutes décimales."""
    if isinstance(t, (time, datetime)):
        return t.hour * 60 + t.minute
    if isinstance(t, (int, float)):
        return t
    return 0

def sont_dans_le_meme_couloir(sj1, sj2):
    """
    Vérifie si deux SuperJobs sont sur le même axe.
    Utilise les listes points_depart et points_arrivee de SuperJob.
    """
    if not sj1 or not sj2 or not sj1.liste_jobs or not sj2.liste_jobs:
        return False
    
    # On compare le premier point du trajet du SJ1 avec le trajet du SJ2
    o1, d1 = sj1.points_depart[0], sj1.points_arrivee[-1]
    o2, d2 = sj2.points_depart[0], sj2.points_arrivee[-1]
    
    return (o1 == o2 and d1 == d2) or (o1 == d2 and d1 == o2)

# =================================================================
# 2. CLASSE POSTE CHAUFFEUR
# =================================================================

class PosteChauffeur:
    def __init__(self, id_poste, vehicule_type, site_initial, params_rh):
        self.id_poste = id_poste
        self.vehicule_type = vehicule_type
        self.stationnement_initial = site_initial
        self.position_actuelle = site_initial
        
        self.etat = 'INACTIF'
        self.job_en_cours = None  # Stockera l'objet SuperJob
        self.job_precedent = None 
        
        self.temps_restant_etat = 0
        self.temps_service_total = 0   
        self.is_pause_faite = False
        
        # Paramètres RH convertis en minutes
        self.amplitude_max = to_decimal_minutes(params_rh.get('v_duree', 450))
        self.duree_pause = to_decimal_minutes(params_rh.get('v_pause', 45))
        self.historique = []

    def enregistrer_evenement(self, minute_actuelle, activite, sj=None, details=""):
        dest_visuelle = self.position_actuelle
        sj_id = "N/A"
        if sj:
            # On prend le flux_id du premier job contenu dans le SuperJob
            sj_id = getattr(sj.liste_jobs[0], 'flux_id', 'SJ')
            if activite == 'EN_MISSION':
                dest_visuelle = sj.points_arrivee[-1]
            elif activite == 'EN_TRAJET_VIDE':
                dest_visuelle = sj.points_depart[0]

        self.historique.append({
            "Poste": self.id_poste,
            "Type": self.vehicule_type,
            "Minute_Debut": minute_actuelle,
            "Heure_Debut": f"{int(minute_actuelle//60):02d}:{int(minute_actuelle%60):02d}",
            "Activite": activite,
            "Origine": self.position_actuelle,
            "Destination": dest_visuelle,
            "SJ_ID": sj_id,
            "Details": details
        })

    def mettre_a_jour(self, pas_temps):
        if self.etat in ['INACTIF', 'FIN_POSTE']: return False
        self.temps_service_total += pas_temps
        if self.temps_restant_etat > 0:
            self.temps_restant_etat -= pas_temps
            return self.temps_restant_etat <= 0
        return True

    def verifier_besoin_pause(self):
        return not self.is_pause_faite and self.temps_service_total >= (self.amplitude_max / 2)

    def verifier_fin_service(self):
        return self.temps_service_total >= self.amplitude_max

    def est_disponible(self):
        return self.etat == 'DISPONIBLE' and self.temps_restant_etat == 0

# =================================================================
# 3. LOGIQUE DE SELECTION
# =================================================================

def calculer_score_stress(sj, temps_actuel):
    """Utilise h_deadline_min et poids_total de SuperJob."""
    temps_restant = sj.h_deadline_min - temps_actuel
    if temps_restant <= 0: return 999999
    # Le poids_total est le temps de mission (camion mobilisé)
    ratio = sj.poids_total / temps_restant
    return ratio * (1 / max(0.001, (1.1 - ratio)))

def trouver_meilleur_job(poste, jobs_dispos, matrice_duree):
    # Filtrage par v_type (attribut de SuperJob)
    candidats = [j for j in jobs_dispos if j.v_type == poste.vehicule_type]
    if not candidats: return None
    
    candidats.sort(key=lambda x: x.score_stress, reverse=True)
    top_candidats = candidats[:5]
    
    # 1. Priorité : Même couloir + déjà sur place
    for j in top_candidats:
        orig_sj = j.points_depart[0]
        if sont_dans_le_meme_couloir(poste.job_precedent, j) and orig_sj == poste.position_actuelle:
            return j
            
    # 2. Priorité : Sur place (évite trajet vide)
    for j in top_candidats:
        orig_sj = j.points_depart[0]
        if orig_sj == poste.position_actuelle:
            return j
            
    # 3. Géographie (plus proche voisin)
    scored = []
    for idx, j in enumerate(top_candidats):
        orig_sj = j.points_depart[0]
        dist = matrice_duree.get(poste.position_actuelle, {}).get(orig_sj, 999)
        scored.append(((idx + (dist / 10)), j))
    scored.sort(key=lambda x: x[0])
    return scored[0][1]

# =================================================================
# 4. ORDONNANCEMENT
# =================================================================

def ordonnancer_journee(liste_sj, n_max_dict, df_vehicules, matrice_duree, params_logistique):
    pas = 5
    rh_params = params_logistique.get('rh', {})
    h_prise = to_decimal_minutes(rh_params.get('h_prise_min', time(6, 0)))
    h_fin = to_decimal_minutes(rh_params.get('h_fin_max', time(21, 0)))
    
    postes = []
    for v_type, n_veh in n_max_dict.items():
        if n_veh <= 0: continue
        df_f = df_vehicules[df_vehicules['Types'] == v_type]
        if df_f.empty: continue
        site_depot = df_f.iloc[0]['Stationnement initial']
        
        for i in range(1, int(n_veh) + 1):
            p = PosteChauffeur(f"{v_type}_{i:02d}", v_type, site_depot, rh_params)
            postes.append(p)

    jobs_restants = [j for j in liste_sj]
    heure_actuelle = h_prise

    while heure_actuelle <= h_fin:
        for p in postes:
            if p.mettre_a_jour(pas):
                sj = p.job_en_cours
                
                if p.etat == 'PRISE_POSTE':
                    p.etat = 'DISPONIBLE'; p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")
                
                elif p.etat == 'EN_TRAJET_VIDE':
                    if sj:
                        # Arrivée au point de départ du SuperJob -> On lance la mission (poids_total)
                        p.etat = 'EN_MISSION'
                        p.temps_restant_etat = sj.poids_total
                        p.position_actuelle = sj.points_depart[0]
                        p.enregistrer_evenement(heure_actuelle, "EN_MISSION", sj, "Exécution SuperJob")
                    else:
                        # Retour dépôt terminé
                        p.position_actuelle = p.stationnement_initial; p.etat = 'DISPONIBLE'
                
                elif p.etat == 'EN_MISSION':
                    # Mission terminée (inclut manutention et trajets internes)
                    if heure_actuelle > sj.h_deadline_min:
                        return {"succes": False, "erreur": f"Retard SJ {sj.liste_jobs[0].flux_id}"}
                    
                    p.position_actuelle = sj.points_arrivee[-1]
                    p.etat = 'DISPONIBLE'; p.job_precedent = sj; p.job_en_cours = None
                    p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")
                
                elif p.etat == 'EN_PAUSE':
                    p.etat = 'DISPONIBLE'; p.enregistrer_evenement(heure_actuelle, "DISPONIBLE")

        # Mise à jour des scores et sélection des nouveaux jobs
        jobs_dispos = [j for j in jobs_restants if j.h_dispo_min <= heure_actuelle]
        for j in jobs_dispos: j.score_stress = calculer_score_stress(j, heure_actuelle)

        for p in postes:
            if p.etat == 'INACTIF':
                if any(j.v_type == p.vehicule_type for j in jobs_dispos):
                    p.etat = 'PRISE_POSTE'; p.temps_restant_etat = 15; p.enregistrer_evenement(heure_actuelle, "PRISE_POSTE")
            
            elif p.est_disponible():
                # Vérification Pause / Fin de service
                if p.verifier_fin_service() or p.verifier_besoin_pause():
                    if p.position_actuelle == p.stationnement_initial:
                        if p.verifier_fin_service():
                            p.etat = 'FIN_POSTE'; p.enregistrer_evenement(heure_actuelle, "FIN_POSTE")
                        else:
                            p.etat = 'EN_PAUSE'; p.temps_restant_etat = p.duree_pause
                            p.is_pause_faite = True; p.enregistrer_evenement(heure_actuelle, "EN_PAUSE")
                    else:
                        # Besoin de rentrer au dépôt pour pause ou fin
                        dist_depot = matrice_duree.get(p.position_actuelle, {}).get(p.stationnement_initial, 30)
                        p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_depot
                        p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", details="Retour Dépôt")
                else:
                    # Recherche de mission
                    sj_choisi = trouver_meilleur_job(p, jobs_dispos, matrice_duree)
                    if sj_choisi:
                        p.job_en_cours = sj_choisi
                        jobs_restants.remove(sj_choisi)
                        jobs_dispos.remove(sj_choisi)
                        
                        orig_sj = sj_choisi.points_depart[0]
                        dist_approche = matrice_duree.get(p.position_actuelle, {}).get(orig_sj, 0)
                        
                        if dist_approche > 0:
                            p.etat = 'EN_TRAJET_VIDE'; p.temps_restant_etat = dist_approche
                            p.enregistrer_evenement(heure_actuelle, "EN_TRAJET_VIDE", sj_choisi)
                        else:
                            p.etat = 'EN_MISSION'; p.temps_restant_etat = sj_choisi.poids_total
                            p.enregistrer_evenement(heure_actuelle, "EN_MISSION", sj_choisi)
        heure_actuelle += pas

    return {"succes": len(jobs_restants) == 0, "postes": postes, "reliquat": len(jobs_restants)}

# =================================================================
# 5. FONCTION D'ENTREE
# =================================================================

def trouver_meilleure_configuration_journee(liste_sj, intensite_par_type, df_vehicules, matrice_duree, params_logistique):
    n_max_initial = { v_type: math.ceil(max(intensites) * 1.2) for v_type, intensites in intensite_par_type.items() }
    res = ordonnancer_journee(liste_sj, n_max_initial, df_vehicules, matrice_duree, params_logistique)
    if res["succes"]:
        return res
    else:
        n_max_safe = {v: count + 1 for v, count in n_max_initial.items()}
        return ordonnancer_journee(liste_sj, n_max_safe, df_vehicules, matrice_duree, params_logistique)





def afficher_gantt_chauffeur_detaille(postes, v_type_selectionne):
    """
    Affiche le planning GANTT pour les objets PosteChauffeur.
    """
    if not postes:
        st.warning("Aucun poste à afficher.")
        return

    data = []
    # CORRECTION : Accès par attribut .vehicule_type et non .get()
    postes_filtres = [p for p in postes if p.vehicule_type == v_type_selectionne]

    if not postes_filtres:
        st.info(f"Aucune activité pour le type de véhicule : {v_type_selectionne}")
        return

    for p in postes_filtres:
        for ev in p.historique:
            # On calcule la fin de l'événement. 
            # Si c'est le dernier événement, on lui donne une durée minimale pour l'affichage
            minute_debut = ev["Minute_Debut"]
            
            # Pour le Gantt, on cherche le début de l'événement suivant pour définir la fin de celui-ci
            # Ou on utilise un pas fixe de 5 minutes si c'est le dernier point connu
            data.append({
                "Poste": p.id_poste,
                "Début": minute_debut,
                "Fin": minute_debut + 5, # Valeur par défaut, Plotly ajustera les blocs adjacents
                "Activité": ev["Activite"],
                "SJ_ID": ev.get("SJ_ID", "N/A"),
                "Détails": ev.get("Details", ""),
                "Heure": ev["Heure_Debut"]
            })

    df = pd.DataFrame(data)

    # Création du graphique
    fig = px.timeline(
        df, 
        x_start="Début", 
        x_end="Fin", 
        y="Poste", 
        color="Activité",
        hover_data=["Heure", "SJ_ID", "Détails"],
        title=f"Planning détaillé - {v_type_selectionne}",
        color_discrete_map={
            "EN_MISSION": "#1f77b4",      # Bleu
            "EN_TRAJET_VIDE": "#ff7f0e",  # Orange
            "DISPONIBLE": "#2ca02c",      # Vert
            "EN_PAUSE": "#d62728",        # Rouge
            "PRISE_POSTE": "#9467bd",     # Violet
            "FIN_POSTE": "#7f7f7f"        # Gris
        }
    )

    # Ajustement des axes pour afficher les minutes/heures correctement
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(xaxis_title="Minutes de la journée", showlegend=True)
    
    st.plotly_chart(fig, use_container_width=True)
