import base64
import os

from odoo import api, fields, models
from odoo.exceptions import ValidationError

AUDIO_MIMETYPES = {
    '.mp3': 'audio/mpeg',
    '.ogg': 'audio/ogg',
    '.oga': 'audio/ogg',
    '.wav': 'audio/wav',
    '.m4a': 'audio/mp4',
    '.aac': 'audio/aac',
}
AUDIO_EXTENSIONS = tuple(AUDIO_MIMETYPES)
MAX_AUDIO_SIZE_MB = 15


class PrayerAzanSound(models.Model):
    _name = 'prayer.azan.sound'
    _description = 'Azan Sound'
    _order = 'sequence, name, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    audio_file = fields.Binary(string='Audio File', attachment=True, required=True,
                               help="Azan recording (MP3, OGG, WAV, M4A or AAC), "
                                    "max %s MB." % MAX_AUDIO_SIZE_MB)
    audio_filename = fields.Char(string='File Name')
    note = fields.Text()
    audio_url = fields.Char(compute='_compute_audio_url')
    location_count = fields.Integer(compute='_compute_location_count')

    @api.depends('write_date')
    def _compute_audio_url(self):
        for sound in self:
            if sound.id:
                unique = sound.write_date and int(sound.write_date.timestamp()) or 0
                sound.audio_url = '/web/content/prayer.azan.sound/%s/audio_file?unique=%s' % (
                    sound.id, unique)
            else:
                sound.audio_url = False

    def _compute_location_count(self):
        groups = self.env['prayer.location']._read_group(
            ['|', ('azan_sound_id', 'in', self.ids), ('fajr_azan_sound_id', 'in', self.ids)],
            ['azan_sound_id', 'fajr_azan_sound_id'], ['__count'])
        counts = {}
        for sound, fajr_sound, count in groups:
            for rec in {sound, fajr_sound}:
                if rec:
                    counts[rec.id] = counts.get(rec.id, 0) + count
        for sound in self:
            sound.location_count = counts.get(sound.id, 0)

    @api.constrains('audio_file', 'audio_filename')
    def _check_audio_file(self):
        for sound in self:
            if not sound.audio_file:
                continue
            extension = os.path.splitext(sound.audio_filename or '')[1].lower()
            if extension not in AUDIO_EXTENSIONS:
                raise ValidationError(self.env._(
                    "The azan file must be an audio file (%(extensions)s).",
                    extensions=', '.join(AUDIO_EXTENSIONS)))
            size = len(base64.b64decode(sound.with_context(bin_size=False).audio_file))
            if size > MAX_AUDIO_SIZE_MB * 1024 * 1024:
                raise ValidationError(self.env._(
                    "The azan file is too large (maximum %(size)s MB).", size=MAX_AUDIO_SIZE_MB))

    @api.model_create_multi
    def create(self, vals_list):
        sounds = super().create(vals_list)
        sounds._sync_audio_mimetype()
        return sounds

    def write(self, vals):
        res = super().write(vals)
        if 'audio_file' in vals or 'audio_filename' in vals:
            self._sync_audio_mimetype()
        return res

    def _sync_audio_mimetype(self):
        """Serve the file with its audio mimetype (the attachment is named after
        the field, so Odoo cannot guess it): some browsers refuse to play
        'application/octet-stream'."""
        attachments = self.env['ir.attachment'].sudo().search([
            ('res_model', '=', self._name),
            ('res_field', '=', 'audio_file'),
            ('res_id', 'in', self.ids),
        ])
        for attachment in attachments:
            sound = self.browse(attachment.res_id)
            extension = os.path.splitext(sound.audio_filename or '')[1].lower()
            mimetype = AUDIO_MIMETYPES.get(extension)
            if mimetype and attachment.mimetype != mimetype:
                attachment.mimetype = mimetype

    def action_view_locations(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._('Locations'),
            'res_model': 'prayer.location',
            'view_mode': 'list,form',
            'domain': ['|', ('azan_sound_id', '=', self.id), ('fajr_azan_sound_id', '=', self.id)],
        }
