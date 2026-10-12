<?php

/** GPL-3.0-or-later. Adapted from OMV's OneDrive module. */

namespace Engined\Module;

class ProtonDrive extends \OMV\Engine\Module\ServiceAbstract implements
    \OMV\Engine\Notify\IListener,
    \OMV\Engine\Module\IServiceStatus,
    \OMV\Engine\Module\INotification
{
    public function getName()
    {
        return 'protondrive';
    }

    public function getStatus()
    {
        $object = \OMV\Config\Database::getInstance()->get('conf.service.protondrive');
        $service = new \OMV\System\SystemCtl('omv-protondrive');
        return [
            'name' => $this->getName(),
            'title' => gettext('Proton Drive'),
            'enabled' => $object->get('enable'),
            'running' => $service->isActive(),
        ];
    }

    public function getNotificationConfig()
    {
        return [
            [
                'id' => 'protondrivebackup',
                'type' => gettext('Backup'),
                'title' => gettext('Proton Drive - Backup failure'),
            ],
            [
                'id' => 'protondriverecovery',
                'type' => gettext('Backup'),
                'title' => gettext('Proton Drive - Container recovery'),
            ],
            [
                'id' => 'protondriveauth',
                'type' => gettext('Backup'),
                'title' => gettext('Proton Drive - Authentication'),
            ],
            [
                'id' => 'protondriveservice',
                'type' => gettext('Backup'),
                'title' => gettext('Proton Drive - Service availability'),
            ],
        ];
    }

    public function deployBefore(): array
    {
        return ['monit'];
    }

    public function bindListeners(\OMV\Engine\Notify\Dispatcher $dispatcher)
    {
        foreach ([OMV_NOTIFY_CREATE, OMV_NOTIFY_MODIFY, OMV_NOTIFY_DELETE] as $event) {
            foreach ([
                'org.openmediavault.conf.service.protondrive',
                'org.openmediavault.conf.service.protondrive.set',
            ] as $path) {
                $dispatcher->addListener($event, $path, [$this, 'setDirty']);
            }
        }
    }
}
