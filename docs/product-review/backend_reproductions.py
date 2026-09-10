"""Product review reproductions; temp files and fake external actions only."""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
# All Storage instances below are rooted in a TemporaryDirectory.
from backend.storage import Storage
from backend.models import SparrowConfig,LibraryItem,MediaType,TorrentClientConfig,TorrentClientType
from backend.agents.models import Job,JobStatus,AgentSession,AgentKind,Mandate,MonitoringMode,Event
from backend.agents.service import AgentService
from backend.agents.runtime import ToolCtx,AgentSpec,ToolDef,MAX_STEPS_PER_WAKE
from backend.agents.tools import fetch_tools,librarian_tools
from backend.services.file_organizer import scan_library,scan_show_episodes

async def noop(*a,**kw): return None
def tool(tools,name): return next(t for t in tools if t.name==name)

async def main():
    results={}
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);(root/'staging').mkdir();(root/'library').mkdir()
        storage=Storage(str(root/'data'));await storage.load_all()
        await storage.save_config(SparrowConfig(staging_dir=str(root/'staging'),library_dir=str(root/'library'),max_active_transfers=1,
           torrent_client=TorrentClientConfig(type=TorrentClientType.QBITTORRENT)))
        service=AgentService(storage,str(root/'data'),noop);service._wake_soon=lambda *a:None
        class Manager:
            async def add_magnet(self,*a,**kw):await asyncio.sleep(.02);return 'fixture-hash'
            async def stop_torrent(self,*a,**kw):return None
            async def delete_torrent(self,*a,**kw):return None
        mgr=Manager()
        async def connect():await asyncio.sleep(.02);return mgr,True,'fixture'
        async def connected():return mgr
        service.toolbox.connect_torrents=connect;service._connected_manager=connected
        job=Job(tmdb_id=1,title='Fixture',wanted_episodes={'1':[1]});session=AgentSession(job_id=job.id)
        job.session_id=session.id;service.store.save_job(job);service.store.save_session(session)
        ctx=ToolCtx(session=session,runtime=service.runtime);add=tool(fetch_tools(service.toolbox),'client_add')
        await asyncio.gather(add.handler(ctx,{'info_hash':'a'*40}),add.handler(ctx,{'info_hash':'b'*40}))
        results['concurrent_limit']={'configured':1,'actual_active':len(storage.get_all_downloads())}
        for d in storage.get_all_downloads():await storage.delete_download(d.id)
        await service.pause_job(job.id)
        try:
            await add.handler(ctx,{'info_hash':'c'*40})
            results['add_after_pause']='Accepted while job status was paused'
        except Exception as e:results['add_after_pause']=str(e)
        await service.cancel_job(job.id)
        try:
            await add.handler(ctx,{'info_hash':'d'*40})
            results['add_after_cancel']='Accepted while job status was abandoned and stored session was closed'
        except Exception as e:results['add_after_cancel']=str(e)
        movie=LibraryItem(id='missing',title='Missing movie',media_type=MediaType.MOVIE,path=str(root/'library'/'absent.mp4'),tmdb_id=2,
                          metadata={'verified':True,'quality':'1080p','audio_languages':['fra']})
        await storage.add_library_item(movie)
        movie_job=Job(tmdb_id=2,title='Missing movie',media_type='movie',audio_pref='english')
        service.store.save_job(movie_job)
        movie_session=AgentSession(job_id=movie_job.id)
        await tool(fetch_tools(service.toolbox),'job_close').handler(ToolCtx(session=movie_session,runtime=service.runtime),{'outcome':'complete','note':'Ready'})
        results['complete_missing_file_wrong_audio']={'path_exists':Path(movie.path).exists(),'job_status':service.store.get_job(movie_job.id).status.value,'audio_preference':movie_job.audio_pref,'recorded_audio':['fra']}
        show=root/'library'/'TV Shows'/'Fixture Show';show.mkdir(parents=True)
        video=show/'Fixture.Show.S01E01.1080p.mkv';video.write_text('This is text, not video.')
        results['scan_text_file']=scan_show_episodes(show)
        # The current Media Agent convention is <library>/<Show>/Season NN.
        agent_folder=root/'library'/'Agent Show'/'Season 01';agent_folder.mkdir(parents=True)
        (agent_folder/'Agent.Show.S01E01.mkv').write_text('Fixture')
        results['scan_agent_layout_found']=[i['title'] for i in scan_library(str(root/'library'))]
        service.store.save_mandate(Mandate(tmdb_id=77,mode=MonitoringMode.KEEP_CURRENT))
        overview=await tool(librarian_tools(service.toolbox,service.create_job),'library_overview').handler(ctx,{})
        results['unowned_monitor_visible_to_librarian']=any(s['tmdb_id']==77 for s in overview['shows'])
        results['single_episode_summary']=Mandate(tmdb_id=1,requested_episodes={'1':[2]}).describe()
        # Real runtime loop, finite scripted model response sequence, no provider.
        calls=0;repeat_session=AgentSession(job_id=job.id);service.store.save_session(repeat_session)
        class Block:
            type='tool_use';name='observe';input={}
            def __init__(self,n):self.id=f'tool-{n}'
            def to_dict(self):return {'type':self.type,'id':self.id,'name':self.name,'input':{}}
        async def fake_api(*args):
            nonlocal calls
            calls+=1
            return SimpleNamespace(content=[Block(calls)] if calls<=MAX_STEPS_PER_WAKE+5 else [],usage=None)
        service.runtime._call_api=fake_api
        spec=AgentSpec(kind='fetch',model=lambda:'fixture',system=noop,tools=lambda s:[ToolDef('observe','Read-only fixture',{'type':'object'},noop)])
        await service.runtime._turn(repeat_session,spec,[Event(kind='nudge')])
        results['wake_step_limit']={'declared_limit':MAX_STEPS_PER_WAKE,'observed_api_calls':calls,'model_stopped_voluntarily':True}
    print(json.dumps(results,indent=2))

asyncio.run(main())
